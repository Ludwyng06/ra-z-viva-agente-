const { spawn } = require('child_process');
const fs = require('fs');
const path = require('path');

async function runVerification() {
  const tmpDir = path.join(require('os').tmpdir(), 'chrome_verify_' + Date.now());
  const outputDir = path.resolve(__dirname, '../tmp');
  fs.mkdirSync(outputDir, { recursive: true });
  const port = 9444;
  const chromeProc = spawn(process.env.CHROME_BIN || 'chrome', [
    '--headless=new',
    '--remote-debugging-port=' + port,
    '--user-data-dir=' + tmpDir,
    '--disable-gpu',
    'http://localhost:8765/'
  ]);

  await new Promise(r => setTimeout(r, 2000));

  let consoleErrors = [];

  try {
    const res = await fetch(`http://localhost:${port}/json`);
    const tabs = await res.json();
    const pageTab = tabs.find(t => t.type === 'page');
    if (!pageTab) throw new Error('No page tab found in Chrome');

    const ws = new WebSocket(pageTab.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = reject;
    });

    let msgId = 1;
    function send(method, params = {}) {
      return new Promise((resolve) => {
        const id = msgId++;
        const handler = (evt) => {
          const data = JSON.parse(evt.data);
          if (data.id === id) {
            ws.removeEventListener('message', handler);
            resolve(data.result);
          }
        };
        ws.addEventListener('message', handler);
        ws.send(JSON.stringify({ id, method, params }));
      });
    }

    // Capture console and runtime error logs
    ws.addEventListener('message', (evt) => {
      const data = JSON.parse(evt.data);
      if (data.method === 'Console.messageAdded') {
        const msg = data.params.message;
        if (msg.level === 'error') {
          consoleErrors.push(`[Console.error] ${msg.text}`);
        }
      }
      if (data.method === 'Runtime.exceptionThrown') {
        const ex = data.params.exceptionDetails;
        consoleErrors.push(`[Exception] ${ex.text} ${ex.exception ? ex.exception.description : ''}`);
      }
      if (data.method === 'Log.entryAdded') {
        const entry = data.params.entry;
        if (entry.level === 'error') {
          consoleErrors.push(`[Log.error] ${entry.text}`);
        }
      }
    });

    await send('Page.enable');
    await send('Runtime.enable');
    await send('Console.enable');
    await send('Log.enable');

    console.log('1. Testing Desktop Layout (1280x800)...');
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1280,
      height: 800,
      deviceScaleFactor: 1,
      mobile: false
    });
    await send('Page.navigate', { url: 'http://localhost:8765/' });
    await new Promise(r => setTimeout(r, 2500));

    // Check overflow and layout properties
    const desktopEval = await send('Runtime.evaluate', {
      expression: `(() => {
        const scrollWidth = document.body.scrollWidth;
        const innerWidth = window.innerWidth;
        const sidebar = document.getElementById('historial');
        const sidebarRect = sidebar ? sidebar.getBoundingClientRect() : null;
        const composer = document.querySelector('.composer');
        const composerRect = composer ? composer.getBoundingClientRect() : null;
        const btnVoz = document.getElementById('btn-voz');
        const btnHablar = document.getElementById('btn-hablar');
        const btnEnviar = document.getElementById('btn-enviar');
        const voiceInSidebar = sidebar ? sidebar.querySelector('#sw-voz, label.switch') : null;
        const empty = document.getElementById('vacio');
        const examples = document.querySelectorAll('.example-card');
        const historyItems = document.querySelectorAll('.history-item');

        return {
          scrollWidth,
          innerWidth,
          hasOverflow: scrollWidth > innerWidth,
          sidebarWidth: sidebarRect ? Math.round(sidebarRect.width) : 0,
          composerWidth: composerRect ? Math.round(composerRect.width) : 0,
          hasBtnVozInComposer: !!(composer && composer.contains(btnVoz)),
          hasBtnHablarInComposer: !!(composer && composer.contains(btnHablar)),
          hasBtnEnviarInComposer: !!(composer && composer.contains(btnEnviar)),
          hasVoiceInSidebar: !!voiceInSidebar,
          examplesCount: examples.length,
          historyCount: historyItems.length,
          hasEmptyWelcome: !!empty
        };
      })()`,
      returnByValue: true
    });

    console.log('Desktop eval result:', JSON.stringify(desktopEval.result.value, null, 2));

    // Capture desktop screenshot to the ignored local tmp directory
    const pcShot = await send('Page.captureScreenshot', { format: 'png' });
    const pcPath = path.join(outputDir, 'copiloto-ui-pc.png');
    fs.writeFileSync(pcPath, Buffer.from(pcShot.data, 'base64'));
    console.log(`Saved desktop screenshot to ${pcPath} (${pcShot.data.length} bytes)`);

    console.log('\n2. Testing Mobile Layout (390x844)...');
    await send('Emulation.setDeviceMetricsOverride', {
      width: 390,
      height: 844,
      deviceScaleFactor: 2,
      mobile: true,
      hasTouch: true
    });
    await new Promise(r => setTimeout(r, 1500));

    const mobileEval = await send('Runtime.evaluate', {
      expression: `(() => {
        const scrollWidth = document.body.scrollWidth;
        const innerWidth = window.innerWidth;
        const sidebar = document.getElementById('historial');
        const sidebarRect = sidebar ? sidebar.getBoundingClientRect() : null;
        const isSidebarHidden = sidebarRect ? (sidebarRect.right <= 0 || sidebarRect.left < 0) : true;
        const chatInner = document.querySelector('.chat-inner');
        const chatStyle = chatInner ? window.getComputedStyle(chatInner) : null;
        const input = document.getElementById('mensaje');

        return {
          scrollWidth,
          innerWidth,
          hasOverflow: scrollWidth > innerWidth,
          isSidebarHidden,
          paddingLeft: chatStyle ? chatStyle.paddingLeft : null,
          paddingRight: chatStyle ? chatStyle.paddingRight : null,
          placeholderText: input ? input.placeholder : null
        };
      })()`,
      returnByValue: true
    });

    console.log('Mobile eval result:', JSON.stringify(mobileEval.result.value, null, 2));

    // Capture mobile screenshot to the ignored local tmp directory
    const movilShot = await send('Page.captureScreenshot', { format: 'png' });
    const movilPath = path.join(outputDir, 'copiloto-ui-movil.png');
    fs.writeFileSync(movilPath, Buffer.from(movilShot.data, 'base64'));
    console.log(`Saved mobile screenshot to ${movilPath} (${movilShot.data.length} bytes)`);

    console.log('\n3. Testing Interaction (Open chat from history, Toggle voice speaker, Send message)...');
    // Reset back to desktop for functional test
    await send('Emulation.setDeviceMetricsOverride', {
      width: 1280,
      height: 800,
      deviceScaleFactor: 1,
      mobile: false
    });
    await new Promise(r => setTimeout(r, 500));

    // Test clicking speaker toggle in composer
    const toggleVoiceRes = await send('Runtime.evaluate', {
      expression: `(() => {
        const btnVoz = document.getElementById('btn-voz');
        const swVoz = document.getElementById('sw-voz');
        const stateBefore = swVoz.checked;
        btnVoz.click();
        const stateAfter = swVoz.checked;
        const hasActiveClass = btnVoz.classList.contains('active');
        btnVoz.click(); // restore
        return { stateBefore, stateAfter, hasActiveClass, restored: swVoz.checked };
      })()`,
      returnByValue: true
    });
    console.log('Toggle voice speaker result:', toggleVoiceRes.result.value);

    // Click first item in history
    const clickHistoryRes = await send('Runtime.evaluate', {
      expression: `(() => {
        const firstItem = document.querySelector('.history-item-main');
        if (firstItem) {
          firstItem.click();
          return { clicked: true, title: firstItem.querySelector('.history-item-title').textContent };
        }
        return { clicked: false };
      })()`,
      returnByValue: true
    });
    console.log('Click history item result:', clickHistoryRes.result.value);

    // Wait for conversation messages to load
    await new Promise(r => setTimeout(r, 2000));

    const checkLoadedConv = await send('Runtime.evaluate', {
      expression: `(() => {
        const messages = document.querySelectorAll('.msg');
        const activeItem = document.querySelector('.history-item.active');
        const titleTopbar = document.getElementById('current-chat-title');
        const trazaOpen = document.getElementById('traza').classList.contains('open');
        return {
          messagesCount: messages.length,
          hasActiveChat: !!activeItem,
          topbarTitle: titleTopbar ? titleTopbar.textContent : null,
          trazaOpen
        };
      })()`,
      returnByValue: true
    });
    console.log('Conversation loaded evaluation:', checkLoadedConv.result.value);

    // Test clicking an assistant message to open Trazabilidad
    const openTrazaRes = await send('Runtime.evaluate', {
      expression: `(() => {
        const asstMsg = document.querySelector('.msg.assistant');
        if (asstMsg) {
          asstMsg.click();
          return { clicked: true, isOpen: document.getElementById('traza').classList.contains('open') };
        }
        return { clicked: false };
      })()`,
      returnByValue: true
    });
    console.log('Open Trazabilidad click result:', openTrazaRes.result.value);

    // Close Trazabilidad
    const closeTrazaRes = await send('Runtime.evaluate', {
      expression: `(() => {
        const btnClose = document.getElementById('btn-traza-close');
        if (btnClose) {
          btnClose.click();
          return { clicked: true, isOpen: document.getElementById('traza').classList.contains('open') };
        }
        return { clicked: false };
      })()`,
      returnByValue: true
    });
    console.log('Close Trazabilidad click result:', closeTrazaRes.result.value);

    // Test "Nuevo chat" button
    const nuevoChatRes = await send('Runtime.evaluate', {
      expression: `(() => {
        const btnNew = document.getElementById('btn-nuevo-chat');
        if (btnNew) {
          btnNew.click();
          return { clicked: true, emptyVisible: !!document.getElementById('vacio') };
        }
        return { clicked: false };
      })()`,
      returnByValue: true
    });
    console.log('Nuevo chat button click result:', nuevoChatRes.result.value);

    // Test sending a message
    console.log('Sending message through form...');
    const sendMsgRes = await send('Runtime.evaluate', {
      expression: `(() => {
        const input = document.getElementById('mensaje');
        const form = document.getElementById('form');
        input.value = '¿Cuáles son los productos naturales más pedidos?';
        form.requestSubmit();
        return { submitted: true };
      })()`,
      returnByValue: true
    });
    console.log('Send message submitted:', sendMsgRes.result.value);

    // Poll for LLM response
    let msgSuccess = false;
    for (let i = 0; i < 20; i++) {
      await new Promise(r => setTimeout(r, 1000));
      const pollRes = await send('Runtime.evaluate', {
        expression: `(() => {
          const msgs = document.querySelectorAll('.msg');
          const userMsg = document.querySelector('.msg.user');
          const asstMsg = document.querySelector('.msg.assistant:not(.typing)');
          const empty = document.getElementById('vacio');
          return {
            totalMsgs: msgs.length,
            hasUserMsg: !!userMsg,
            hasAsstMsg: !!asstMsg,
            asstText: asstMsg ? asstMsg.textContent.slice(0, 100) : null,
            welcomeScreenRemoved: !empty
          };
        })()`,
        returnByValue: true
      });
      if (pollRes.result.value.hasAsstMsg) {
        console.log(`Assistant replied after ${i + 1}s:`, pollRes.result.value);
        msgSuccess = true;
        break;
      }
    }
    if (!msgSuccess) {
      console.warn('Warning: LLM took longer than 20s to respond.');
    }

    // Check console error count
    console.log('\nTotal Console Errors:', consoleErrors.length);
    if (consoleErrors.length > 0) {
      console.error('Errors encountered:', consoleErrors);
    } else {
      console.log('CONGRATULATIONS: 0 console errors!');
    }

    await new Promise(r => setTimeout(r, 500));
    ws.close();
  } finally {
    chromeProc.kill();
  }
}

runVerification().catch(err => {
  console.error('Verification failed:', err);
  process.exit(1);
});
