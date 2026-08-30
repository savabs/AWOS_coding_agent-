#!/usr/bin/env node
/**
 * e2e_test.js — Comprehensive end-to-end test for AWOS UI
 * Tests: page load, model selector, chat streaming, badge, actions, errors
 */
const { chromium } = require('playwright');

const PORT = 8765;
const URL = `http://127.0.0.1:${PORT}/agent`;

const PASS = '✅'; const FAIL = '❌';
let passed = 0, failed = 0;
function check(name, condition, detail) {
  if (condition) { console.log(`  ${PASS} ${name}`); passed++; }
  else { console.log(`  ${FAIL} ${name}${detail ? ' — ' + detail : ''}`); failed++; }
}

async function main() {
  console.log('🔍 AWOS End-to-End Test\n');
  const browser = await chromium.launch({ headless: true, executablePath: '/usr/bin/google-chrome', args: ['--no-sandbox','--disable-gpu'] });
  
  // ── TEST 1: Page Load ──
  console.log('── TEST 1: Page Load ──');
  const page = await browser.newPage({ viewport: { width: 1280, height: 800 } });
  const errors = [];
  page.on('pageerror', e => errors.push(e.message));
  page.on('console', msg => { if (msg.type() === 'error') errors.push(msg.text()); });
  
  try {
    await page.goto(URL, { waitUntil: 'domcontentloaded', timeout: 10000 });
    check('Page loads', true);
  } catch(e) {
    check('Page loads', false, e.message);
  }
  await page.waitForTimeout(500);
  check('No JS errors on load', errors.length === 0, errors.join('; '));
  
  // ── TEST 2: Key Elements ──
  console.log('\n── TEST 2: Key Elements ──');
  for (const id of ['prompt-input', 'prompt-btn', 'model-select', 'conv', 'empty-state', 'conv-inner']) {
    const el = await page.$('#' + id);
    check('#' + id + ' exists', !!el);
  }
  
  // ── TEST 3: Model Selector ──
  console.log('\n── TEST 3: Model Selector ──');
  const selValue = await page.$eval('#model-select', el => el.value);
  check('Default is Auto (empty value)', selValue === '');
  
  const optCount = await page.$eval('#model-select', el => el.options.length);
  check('Has options', optCount >= 5, optCount + ' options');
  
  const autoOpt = await page.$eval('#model-select', el => el.options[0].textContent);
  check('First option is Auto', autoOpt.includes('Auto'), autoOpt);
  
  // ── TEST 4: Send query with Auto ──
  console.log('\n── TEST 4: Auto routing chat ──');
  await page.fill('#prompt-input', 'What is 2+2? Just the number.');
  await page.click('#prompt-btn');
  
  // Wait for response
  try {
    await page.waitForFunction(() => {
      const b = document.querySelector('.msg.assistant .msg-bubble');
      return b && b.textContent.length > 3 && !document.querySelector('.msg.assistant.streaming');
    }, { timeout: 60000 });
    check('Response received', true);
  } catch(e) {
    check('Response received', false, e.message);
  }
  
  // Check badge elements
  const metaHTML = await page.$eval('.msg.assistant .msg-meta', el => el.innerHTML).catch(() => '');
  const hasModel = metaHTML.includes('class="model"');
  const hasTime = metaHTML.includes('class="time"');
  const hasCost = metaHTML.includes('class="cost"');
  check('Badge: model name', hasModel);
  check('Badge: timing info', hasTime);
  check('Badge: cost info', hasCost, metaHTML.substring(0, 100));
  
  // Check message actions
  const actions = await page.$('.msg-actions');
  check('Message actions exist', !!actions);
  if (actions) {
    const btns = await actions.$$('button');
    check('3 action buttons', btns.length === 3, btns.length + ' buttons');
  }
  
  // Check no JS code leaked
  const bubbleHTML = await page.$eval('.msg.assistant .msg-bubble', el => el.innerHTML);
  const hasLeakedJS = bubbleHTML.includes('setTimeout') || bubbleHTML.includes('this.textContent');
  check('No JS leaked into response', !hasLeakedJS);
  
  // ── TEST 5: Send query with specific model ──
  console.log('\n── TEST 5: Forced model routing ──');
  await page.selectOption('#model-select', 'deepseek-v4-flash');
  check('Model selected', (await page.$eval('#model-select', el => el.value)) === 'deepseek-v4-flash');
  
  await page.fill('#prompt-input', 'Say hi in ONE word only.');
  await page.click('#prompt-btn');
  
  try {
    await page.waitForFunction(() => {
      const bubbles = document.querySelectorAll('.msg.assistant .msg-bubble');
      return bubbles.length >= 2 && bubbles[1].textContent.length > 1 && !document.querySelector('.msg.assistant.streaming');
    }, { timeout: 60000 });
    check('Forced model response received', true);
  } catch(e) {
    check('Forced model response received', false, e.message);
  }
  
  // Check the second message exists
  const msgCount = await page.$$eval('.msg.assistant', els => els.length);
  check('Two assistant messages', msgCount >= 2, msgCount + ' messages');
  
  // ── TEST 6: Copy button ──
  console.log('\n── TEST 6: Copy action ──');
  const copyBtn = await page.$('.msg-act');
  check('Copy button exists', !!copyBtn);
  
  // ── TEST 7: Errors ──
  console.log('\n── TEST 7: Console errors ──');
  check('No page errors', errors.length === 0, errors.join('; '));
  
  // ── Summary ──
  console.log('\n' + '='.repeat(40));
  console.log(`RESULTS: ${passed} passed, ${failed} failed`);
  console.log('='.repeat(40));
  
  await browser.close();
  process.exit(failed > 0 ? 1 : 0);
}

main().catch(e => { console.error(e); process.exit(1); });
