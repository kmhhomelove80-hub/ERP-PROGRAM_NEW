(() => {
  const open = document.querySelector('#scan-open');
  const dialog = document.querySelector('#scan-dialog');
  const video = document.querySelector('#scan-video');
  const status = document.querySelector('#scan-status');
  const form = document.querySelector('#stock-filter');
  let stream, timer, generation = 0;
  function stop() {
    generation++;
    clearTimeout(timer);
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    video.srcObject = null;
  }
  dialog.addEventListener('close', stop);
  document.querySelector('#scan-close').addEventListener('click', () => dialog.close());
  window.addEventListener('pagehide', stop);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && dialog.open) dialog.close();
  });
  open.addEventListener('click', async () => {
    stop();
    const current = generation;
    dialog.showModal();
    status.textContent = '카메라 준비 중…';
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia || !window.BarcodeDetector) {
      status.textContent = '이 환경에서는 카메라 검색을 지원하지 않습니다. HTTPS와 지원 브라우저를 확인하거나 검색창에 코드를 직접 입력하세요. 외부 스캐너도 사용할 수 있습니다.';
      return;
    }
    try {
      const supported = await BarcodeDetector.getSupportedFormats();
      const formats = ['qr_code', 'code_128', 'code_39', 'ean_13', 'ean_8', 'itf', 'upc_a', 'upc_e', 'data_matrix'].filter(format => supported.includes(format));
      if (current !== generation || !dialog.open) return;
      if (!formats.length) throw new Error('unsupported');
      const detector = new BarcodeDetector({formats});
      const acquired = await navigator.mediaDevices.getUserMedia({audio: false, video: {facingMode: {ideal: 'environment'}}});
      if (current !== generation || !dialog.open) {
        acquired.getTracks().forEach(track => track.stop());
        return;
      }
      stream = acquired;
      video.srcObject = stream;
      await video.play();
      status.textContent = 'ERP 코드 또는 위치 라벨을 카메라에 비춰 주세요.';
      async function detect() {
        if (current !== generation || !dialog.open) return;
        try {
          const codes = await detector.detect(video);
          if (current !== generation || !dialog.open) return;
          const value = codes.map(code => (code.rawValue || '').trim()).find(Boolean);
          if (value) {
            if (value.length > 250 || /[\x00-\x1f]/.test(value)) {
              status.textContent = 'ERP 코드 또는 위치가 담긴 라벨을 읽어 주세요. 최대 250자입니다.';
            } else {
              // Treat scanned data as search text, never as a URL or HTML.
              form.elements.q.value = value;
              dialog.close();
              form.requestSubmit();
              form.elements.q.focus();
              return;
            }
          }
        } catch (error) {
          if (current === generation) status.textContent = '라벨을 읽지 못했습니다. 각도와 밝기를 조정하거나 직접 입력하세요.';
        }
        if (current === generation) timer = setTimeout(detect, 250);
      }
      detect();
    } catch (error) {
      if (current !== generation) return;
      stop();
      status.textContent = '카메라를 시작하지 못했습니다. 카메라 권한·브라우저 지원을 확인하거나 검색창에 코드를 직접 입력하세요.';
    }
  });
})();
