#!/usr/bin/env node
/**
 * visual_verify.js — Headless browser screenshot + DOM inspection for AWOS UI.
 * Usage: node scripts/visual_verify.js [--message "your query"]
 */
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');

const PORT = process.env.PORT || 8765;
const URL = `http://127.0.0.1:${PORT}/agent`;
const MESSAGE = process.argv.includes('--message') 
  ? process.argv[process.argv.indexOf('--message') + 1] 
  : 'What is Python? Answer in 3 sentences.';

async function main() {
  console.log(`🔍 Launching headless Chrome → ${URL}`);
  const browser = await chromium.launch({ 
    headless: true,
    executablePath: '/usr/bin/google-chrome',
    args: ['--no-sandbox', '--disable-gpu']
  });
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });

  // Collect console logs
  const consoleLogs = [];
  page.on('console', msg => consoleLogs.push(`[${msg.type()}] ${msg.text()}`));

  // Collect page errors
  const pageErrors = [];
  page.on('pageerror', err => pageErrors.push(err.message));

  await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 15000 });

  // Screenshot 1: empty state
  await page.screenshot({ path: '/tmp/awos_empty.png', fullPage: false });
  console.log('📸 Screenshot: empty state → /tmp/awos_empty.png');

  // Check for JavaScript errors
  if (pageErrors.length > 0) {
    console.log('❌ JS ERRORS:', pageErrors);
  } else {
    console.log('✅ No JS errors');
  }

  // Check key elements exist
  const checks = {};
  for (const el of ['prompt-input', 'prompt-btn', 'empty-state', 'conv-inner']) {
    checks[el] = await page.$(`#${el}`) !== null;
  }
  console.log('🧩 DOM elements:', JSON.stringify(checks));

  // Send a message
  console.log(`📤 Sending: "${MESSAGE}"`);
  await page.fill('#prompt-input', MESSAGE);
  await page.click('#prompt-btn');

  // Wait for response to complete
  try {
    // Wait for assistant message bubble with actual content
    console.log('⏳ Waiting for response...');
    await page.waitForFunction(() => {
      const bubble = document.querySelector('.msg.assistant .msg-bubble');
      if (!bubble) return false;
      const text = bubble.textContent || '';
      // Wait until we have substantial content and streaming stopped
      return text.length > 20 && !document.querySelector('.msg.assistant.streaming');
    }, { timeout: 60000 });
    
    // Extra wait for final rendering
    await page.waitForTimeout(1000);

    // Check thinking panel
    const hasThinking = await page.$('.thinking-toggle') !== null;
    const thinkingCollapsed = hasThinking ? await page.$eval('.thinking-toggle', el => el.classList.contains('collapsed')) : null;
    const thinkingText = hasThinking ? await page.$eval('.thinking-panel', el => el.textContent?.length || 0) : 0;
    console.log(`💭 Thinking: ${hasThinking ? (thinkingCollapsed ? 'collapsed' : 'expanded') + ` (${thinkingText} chars)` : 'none'}`);

    // Deep DOM inspection
    const domDump = await page.evaluate(() => {
      const msgs = document.querySelectorAll('.msg.assistant');
      const results = [];
      msgs.forEach((msg, i) => {
        const meta = msg.querySelector('.msg-meta');
        const bubble = msg.querySelector('.msg-bubble');
        results.push({
          index: i,
          hasMeta: !!meta,
          metaVisible: meta ? (meta.offsetParent !== null) : false,
          metaHTML: meta ? meta.innerHTML.substring(0, 200) : 'none',
          metaText: meta ? meta.textContent.trim().substring(0, 100) : 'none',
          bubbleChars: bubble ? bubble.textContent.length : 0,
          bubbleHTML: bubble ? bubble.innerHTML.substring(0, 100) : 'none',
          isStreaming: msg.classList.contains('streaming'),
        });
      });
      return results;
    });
    console.log('🔬 DOM inspection:');
    domDump.forEach(r => {
      console.log(`  msg[${r.index}]: meta="${r.metaText}" | bubble=${r.bubbleChars}c | streaming=${r.isStreaming}`);
      if (r.metaHTML !== 'none') console.log(`    meta HTML: ${r.metaHTML}`);
    });

    // Check for copy buttons
    const copyBtns = await page.$$('.copy-btn');
    console.log(`📋 Copy buttons: ${copyBtns.length}`);

    // Check for syntax highlighting (hljs classes)
    const hljsEls = await page.$$('.hljs, [class*="language-"]');
    console.log(`🎨 Highlighted blocks: ${hljsEls.length}`);

    // Screenshot
    await page.screenshot({ path: '/tmp/awos_response.png', fullPage: false });
    console.log('📸 Screenshot saved');

  } catch (e) {
    console.log('⚠️  Timeout waiting for response:', e.message);
    await page.screenshot({ path: '/tmp/awos_timeout.png', fullPage: false });
  }

  // Summary
  console.log('\n' + '='.repeat(50));
  console.log('VERDICT:');

  const issues = [];
  if (pageErrors.length > 0) issues.push(`${pageErrors.length} JS error(s)`);
  if (!checks['prompt-input']) issues.push('prompt-input missing');
  if (!checks['prompt-btn']) issues.push('prompt-btn missing');
  if (!checks['conv-inner']) issues.push('conv-inner missing');

  const awosLogs = consoleLogs.filter(l => l.includes('[AWOS]'));
  if (awosLogs.length === 0) issues.push('No [AWOS] console logs (SSE handlers not firing)');
  else console.log(`📋 ${awosLogs.length} AWOS console log(s):`);
  awosLogs.forEach(l => console.log('   ' + l.substring(0, 150)));

  if (issues.length === 0) {
    console.log('✅ ALL CHECKS PASSED — UI is rendering correctly');
  } else {
    console.log('❌ ISSUES FOUND:');
    issues.forEach(i => console.log('   - ' + i));
  }

  await browser.close();
}

main().catch(e => { console.error(e); process.exit(1); });
