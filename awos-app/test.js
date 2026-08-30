#!/usr/bin/env node
/**
 * AWOS Visual Tester — interacts with the Agent Console and reports state.
 * 
 * Usage:
 *   npx electron test.js                    # capture idle state
 *   npx electron test.js --run "fix tests"  # type goal and press Enter
 *   npx electron test.js --click "#prompt-btn"  # click any element
 *   npx electron test.js --type "#prompt-input" "fix auth" --wait 2
 *   npx electron test.js --cmd-palette      # open command palette
 *   npx electron test.js --history          # click a history item
 */
const { app, BrowserWindow } = require('electron');

const WAIT = 4;

// Parse -- commands
const args = process.argv.slice(2);
const DELAY = parseInt(args[args.indexOf('--wait') + 1]) || WAIT;
const RUN = args.includes('--run') ? args[args.indexOf('--run') + 1] : null;
const CLICK = args.includes('--click') ? args[args.indexOf('--click') + 1] : null;
const TYPE_SEL = args.includes('--type') ? args[args.indexOf('--type') + 1] : null;
const TYPE_TEXT = TYPE_SEL ? args[args.indexOf('--type') + 2] : null;
const CMD_PALETTE = args.includes('--cmd-palette');
const HISTORY = args.includes('--history');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 1280, height: 800, show: false,
    backgroundColor: '#1a1a1e',
    webPreferences: { nodeIntegration: true, contextIsolation: false }
  });

  await win.loadURL('http://127.0.0.1:8765/agent');
  await sleep(2000);

  // ── Execute actions ──────────────────────────────────────────────────
  
  if (CMD_PALETTE) {
    console.log('▶ Opening command palette...');
    await win.webContents.executeJavaScript(`
      const ov = document.getElementById('palette-overlay');
      ov.style.display = 'flex';
      document.getElementById('palette-input').focus();
    `);
    await sleep(1000);
  }

  if (HISTORY) {
    console.log('▶ Clicking first history item...');
    await win.webContents.executeJavaScript(`
      const items = document.querySelectorAll('.history-entry');
      if (items.length > 0) items[0].click();
    `);
    await sleep(1000);
  }

  if (CLICK) {
    console.log(`▶ Clicking "${CLICK}"...`);
    await clickElement(win, CLICK);
    await sleep(1000);
  }

  if (TYPE_SEL && TYPE_TEXT) {
    console.log(`▶ Typing "${TYPE_TEXT}" into "${TYPE_SEL}"...`);
    await win.webContents.executeJavaScript(`
      const el = document.querySelector('${TYPE_SEL}');
      if (el) { el.value = '${TYPE_TEXT.replace(/'/g, "\\'")}'; el.dispatchEvent(new Event('input', {bubbles:true})); }
    `);
    await sleep(500);
  }

  if (RUN) {
    console.log(`▶ Running goal: "${RUN}"...`);
    await typeAndRun(win, RUN);
    // Wait for activity to start
    await sleep(DELAY * 1000);
  }

  // ── Capture state ────────────────────────────────────────────────────
  await sleep(1000);
  
  const state = await captureSimple(win);
  if (state.error) {
    console.log('ERROR in capture:', state.error);
  } else {
    printReport(state);
  }

  app.quit();
});

// ── Helpers ──────────────────────────────────────────────────────────────
function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }

async function clickElement(win, selector) {
  await win.webContents.executeJavaScript(`
    (function() {
      const el = document.querySelector('${selector}');
      if (el) {
        el.scrollIntoView({block:'center'});
        el.click();
        return 'clicked: ' + '${selector}';
      }
      return 'NOT FOUND: ${selector}';
    })();
  `).then(r => console.log('  Click result:', r));
}

async function typeAndRun(win, text) {
  const result = await win.webContents.executeJavaScript(`
    (async function() {
      const inp = document.getElementById('prompt-input');
      if (!inp) return 'NO INPUT FOUND';
      
      // Type each character with events
      inp.value = '';
      inp.focus();
      const chars = '${text.replace(/'/g, "\\'").replace(/"/g, '\\"')}';
      for (const ch of chars) {
        inp.value += ch;
        inp.dispatchEvent(new InputEvent('input', {bubbles:true, data:ch}));
        await new Promise(r => setTimeout(r, 30));
      }
      
      // Click Run button
      const btn = document.getElementById('prompt-btn');
      if (btn && !btn.disabled) btn.click();
      
      return 'Typed and ran: ${text.replace(/'/g, "\\'")}';
    })();
  `);
  console.log('  Run result:', result);
}

async function captureSimple(win) {
  return await win.webContents.executeJavaScript(`
    (function() {
      try {
        var pi = document.getElementById('prompt-input');
        var pb = document.getElementById('prompt-btn');
        var ph = document.getElementById('prompt-heading');
        var fa = document.getElementById('feed-area');
        var fb = document.getElementById('feed-body');
        var ca = document.getElementById('chat-answer');
        var err = document.getElementById('error-banner');
        var ra = document.getElementById('result-area');
        var hl = document.getElementById('history-list');
        var pa = document.getElementById('palette-overlay');
        
        var feedEls = fb ? fb.querySelectorAll('.feed-item') : null;
        var histEls = hl ? hl.querySelectorAll('.history-entry') : null;
        
        var cs = function(el) { return el ? window.getComputedStyle(el).display : 'none'; };
        
        return {
          visible: {
            prompt: !!(pi && pi.offsetParent),
            feed: !!(fa && cs(fa) !== 'none'),
            chat: !!(ca && cs(ca) !== 'none'),
            result: !!(ra && cs(ra) !== 'none'),
            error: !!(err && cs(err) !== 'none'),
            palette: !!(pa && cs(pa) !== 'none'),
            history: !!(hl && histEls && histEls.length > 0),
          },
          elements: {
            input: pi ? {v:pi.value||'',ph:pi.placeholder||'',d:pi.disabled,f:pi===document.activeElement} : null,
            btn: pb ? {t:pb.textContent.trim(),d:pb.disabled,r:pb.getBoundingClientRect().width+'x'+pb.getBoundingClientRect().height} : null,
          },
          content: {
            heading: ph ? ph.textContent : '',
            feed_count: feedEls ? feedEls.length : 0,
            feed_last5: feedEls ? (function(){var a=[];for(var i=Math.max(0,feedEls.length-5);i<feedEls.length;i++){a.push(feedEls[i].textContent.trim().slice(0,100));}return a;})() : [],
          },
          history_count: histEls ? histEls.length : 0,
          js: {
            running: typeof running!=='undefined'?running:'??',
            feedItems: typeof feedItems!=='undefined'?feedItems:'??',
            sessionCost: typeof sessionCost!=='undefined'?sessionCost:'??',
            sse: typeof eventSource!=='undefined'?(eventSource?'connected':'disconnected'):'??',
          },
        };
      } catch(e) { return {error: e.message, stack: e.stack}; }
    })();
  `);
}

function printReport(s) {
  console.log('\n══════════════ VISUAL STATE ══════════════');
  
  console.log('\nVisible:');
  for (const [k, v] of Object.entries(s.visible || {})) {
    console.log(`  ${v ? '✅' : '⬚'} ${k}`);
  }

  console.log('\nElements:');
  if (s.elements?.input) {
    const i = s.elements.input;
    console.log(`  📝 input:  "${i.v}" [${i.d ? 'DISABLED' : 'enabled'}] [${i.f ? 'FOCUSED' : 'blurred'}]`);
  } else { console.log('  ❌ input MISSING'); }
  if (s.elements?.btn) {
    const b = s.elements.btn;
    console.log(`  ▶ btn:    "${b.t}" [${b.d ? 'DISABLED' : 'enabled'}] [${b.r}]`);
  } else { console.log('  ❌ btn MISSING'); }

  console.log(`\nContent:`);
  console.log(`  Heading: "${s.content?.heading}"`);
  console.log(`  Feed: ${s.content?.feed_count} items`);
  if (s.content?.feed_last5?.length) {
    s.content.feed_last5.forEach(t => console.log(`    · ${t}`));
  }

  console.log(`\nHistory: ${s.history_count} items`);
  console.log(`JS: running=${s.js?.running} feedItems=${s.js?.feedItems} cost=${s.js?.sessionCost} sse=${s.js?.sse}`);

  // Bug detection
  const bugs = [];
  if (!s.elements?.input) bugs.push('prompt-input MISSING');
  if (!s.elements?.btn) bugs.push('prompt-btn MISSING');
  if (s.elements?.btn?.d) bugs.push('prompt-btn DISABLED at idle');
  if (s.elements?.input?.d) bugs.push('prompt-input DISABLED at idle');
  if (s.visible?.palette) bugs.push('palette OPEN (should be closed)');
  if (s.visible?.feed) bugs.push('feed AREA visible at idle (should be hidden)');

  console.log(bugs.length ? '\n🐛 BUGS:' : '\n✅ No bugs');
  bugs.forEach(b => console.log(`  ${b}`));
  console.log('');
}
