#!/usr/bin/env node
/**
 * visual_proof.js — Full visual proof: element tree + color-coded DOM + ASCII screenshot
 */
const { chromium } = require('playwright');

const PORT = 8765;
const URL = `http://127.0.0.1:${PORT}/agent`;

async function main() {
  const browser = await chromium.launch({ 
    headless: true, executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu']
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 15000 });

  // Send query
  await page.fill('#prompt-input', 'What is Python? Answer in 3 sentences.');
  await page.click('#prompt-btn');

  // Wait for response
  await page.waitForFunction(() => {
    const bubble = document.querySelector('.msg.assistant .msg-bubble');
    return bubble && bubble.textContent.length > 50 && !document.querySelector('.msg.assistant.streaming');
  }, { timeout: 60000 });
  await page.waitForTimeout(1000);

  // ── 1. DOM element tree with positions ──
  console.log('='.repeat(70));
  console.log('PROOF 1: Rendered DOM Element Tree');
  console.log('='.repeat(70));
  
  const tree = await page.evaluate(() => {
    function describe(el, depth = 0) {
      if (!el || !el.tagName) return '';
      const rect = el.getBoundingClientRect ? el.getBoundingClientRect() : null;
      const indent = '  '.repeat(depth);
      const pos = rect ? `[${Math.round(rect.x)},${Math.round(rect.y)} ${Math.round(rect.width)}x${Math.round(rect.height)}]` : '[no rect]';
      const id = el.id ? ` #${el.id}` : '';
      const cls = el.className && typeof el.className === 'string' ? ` .${el.className.split(' ').slice(0,3).join('.')}` : '';
      const text = (el.textContent || '').trim().substring(0, 60).replace(/\n/g, ' ');
      const visible = rect && rect.width > 0 && rect.height > 0 ? '👁' : '🙈';
      let line = `${indent}${visible} <${el.tagName.toLowerCase()}${id}${cls}> ${pos} "${text}"`;
      
      // Children
      const children = [];
      for (const child of el.children) {
        children.push(describe(child, depth + 1));
      }
      return line + (children.length ? '\n' + children.join('\n') : '');
    }
    // Start from the conversation area
    const conv = document.getElementById('conv');
    return describe(conv, 0);
  });
  console.log(tree);

  // ── 2. Key element contents ── 
  console.log('\n' + '='.repeat(70));
  console.log('PROOF 2: Key Element Contents');
  console.log('='.repeat(70));

  const contents = await page.evaluate(() => {
    const results = {};
    
    // Model badge
    const meta = document.querySelector('.msg.assistant .msg-meta');
    if (meta) {
      results.model_badge = {
        html: meta.innerHTML,
        text: meta.textContent.trim(),
        visible: meta.offsetParent !== null,
        children: Array.from(meta.children).map(c => ({
          tag: c.tagName, class: c.className, text: c.textContent.trim()
        }))
      };
    }

    // Response bubble
    const bubble = document.querySelector('.msg.assistant .msg-bubble');
    if (bubble) {
      results.response = {
        chars: bubble.textContent.length,
        preview: bubble.textContent.trim().substring(0, 200),
        hasCodeBlocks: bubble.querySelectorAll('pre').length,
        hasCopyBtns: bubble.querySelectorAll('.copy-btn').length,
        hasHighlighted: bubble.querySelectorAll('.hljs').length,
      };
    }

    // Thinking panel
    const thinking = document.querySelector('.thinking-toggle');
    if (thinking) {
      const panel = document.querySelector('.thinking-panel');
      results.thinking = {
        visible: thinking.offsetParent !== null,
        expanded: !thinking.classList.contains('collapsed'),
        chars: panel ? panel.textContent.length : 0,
        preview: panel ? panel.textContent.trim().substring(0, 150) : '',
      };
    } else {
      results.thinking = { present: false };
    }

    // Overall layout
    results.layout = {
      viewport: `${window.innerWidth}x${window.innerHeight}`,
      msgCount: document.querySelectorAll('.msg').length,
      emptyState: document.getElementById('empty-state') ? document.getElementById('empty-state').offsetParent !== null : false,
      titlebar: document.getElementById('titlebar')?.offsetParent !== null,
      inputArea: document.getElementById('input-area')?.offsetParent !== null,
    };

    return results;
  });

  console.log(JSON.stringify(contents, null, 2));

  await browser.close();
}

main().catch(e => { console.error(e); process.exit(1); });
