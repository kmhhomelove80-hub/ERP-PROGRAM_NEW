(() => {
  const open = document.querySelector('#scan-open');
  const dialog = document.querySelector('#scan-dialog');
  const video = document.querySelector('#scan-video');
  const status = document.querySelector('#scan-status');
  const form = document.querySelector('#stock-filter');
  let stream, timer, generation = 0;
  const photo = document.querySelector('#scan-photo');
  const canvas = document.createElement('canvas');
  const context = canvas.getContext('2d', {willReadFrequently: true});
  function decodeImage(source, width, height, maxDimension = 1600, rotation = 0) {
    const scale = Math.min(1, maxDimension / Math.max(width, height));
    const scaledWidth = Math.max(1, Math.round(width * scale));
    const scaledHeight = Math.max(1, Math.round(height * scale));
    canvas.width = rotation === 90 ? scaledHeight : scaledWidth;
    canvas.height = rotation === 90 ? scaledWidth : scaledHeight;
    context.fillStyle = '#fff';
    context.fillRect(0, 0, canvas.width, canvas.height);
    context.save();
    context.translate(canvas.width / 2, canvas.height / 2);
    context.rotate(rotation * Math.PI / 180);
    context.drawImage(source, -scaledWidth / 2, -scaledHeight / 2, scaledWidth, scaledHeight);
    context.restore();
    const rgba = context.getImageData(0, 0, canvas.width, canvas.height).data;
    const gray = new Uint8ClampedArray(canvas.width * canvas.height);
    for (let i = 0; i < gray.length; i++) gray[i] = (rgba[i*4] + 2*rgba[i*4+1] + rgba[i*4+2]) / 4;
    const reader = new ZXing.MultiFormatReader();
    const bitmap = new ZXing.BinaryBitmap(new ZXing.HybridBinarizer(new ZXing.RGBLuminanceSource(gray, canvas.width, canvas.height)));
    try { return reader.decode(bitmap, new Map([[ZXing.DecodeHintType.TRY_HARDER, true]])).getText(); } finally { reader.reset(); }
  }
  function accept(value) {
    value = (value || '').trim();
    if (!value || value.length > 100 || /[\x00-\x1f]/.test(value)) {
      status.textContent = '제품 ERP 코드가 담긴 라벨을 읽어 주세요. 최대 100자이며 코드를 잘라 저장하지 않습니다.';
      return false;
    }
    form.elements.q.value = value;
    form.elements.scan.value = '1';
    form.elements.status.value = '';
    dialog.close();
    form.requestSubmit();
    return true;
  }
  photo.addEventListener('change', async () => {
    const file = photo.files[0];
    if (!file) return;
    photo.value = '';
    stop();
    const current = generation;
    status.textContent = '사진에서 라벨을 읽는 중…';
    const url = URL.createObjectURL(file);
    try {
      const img = new Image();
      img.src = url;
      await img.decode();
      if (current !== generation || !dialog.open) return;
      const attempts = [[2560, 0], [2560, 90], [1600, 0], [1600, 90]];
      for (let i = 0; i < attempts.length; i++) {
        status.textContent = `사진에서 코드를 읽는 중… (${i + 1}/${attempts.length})`;
        await new Promise(resolve => setTimeout(resolve, 0));
        if (current !== generation || !dialog.open) return;
        try {
          const value = decodeImage(img, img.naturalWidth, img.naturalHeight, ...attempts[i]);
          if (accept(value)) return;
        } catch (error) { /* Try the other orientation and resolution. */ }
      }
      throw new Error('No supported barcode found');
    } catch (error) {
      if (current === generation) status.textContent = '사진에서 코드를 찾지 못했습니다. 라벨이 선명하고 크게 보이도록 다시 촬영해 주세요.';
    } finally { URL.revokeObjectURL(url); }
  });
  function stop() {
    generation++;
    clearTimeout(timer);
    if (stream) stream.getTracks().forEach(track => track.stop());
    stream = null;
    video.srcObject = null;
    video.hidden = true;
  }
  dialog.addEventListener('close', stop);
  document.querySelector('#scan-close').addEventListener('click', () => dialog.close());
  window.addEventListener('pagehide', stop);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden && dialog.open) {
      // A native camera/photo picker can background the browser. Keep the dialog
      // open so the chosen image can be decoded when the user returns.
      const hadCamera = Boolean(stream);
      stop();
      if (hadCamera) status.textContent = '실시간 카메라를 중지했습니다. 사진을 선택하거나 닫은 뒤 다시 스캔하세요.';
    }
  });
  open.addEventListener('click', async () => {
    stop();
    const current = generation;
    dialog.showModal();
    status.textContent = '카메라 준비 중…';
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      status.textContent = '현재 접속에서는 실시간 카메라를 사용할 수 없습니다. 사진을 촬영하거나 선택하면 코드를 읽어 위치·수량을 바로 조회합니다. 연속 스캔은 HTTPS 접속이 필요합니다.';
      photo.click();
      return;
    }
    try {
      let detector;
      if (window.BarcodeDetector) {
        try {
          const supported = await BarcodeDetector.getSupportedFormats();
          const formats = ['qr_code', 'code_128', 'code_39', 'ean_13', 'ean_8', 'itf', 'upc_a', 'upc_e', 'data_matrix'].filter(format => supported.includes(format));
          if (formats.length) detector = new BarcodeDetector({formats});
        } catch (error) { /* Local decoder supports browsers without the native API. */ }
      }
      if (current !== generation || !dialog.open) return;
      const acquired = await navigator.mediaDevices.getUserMedia({audio: false, video: {facingMode: {ideal: 'environment'}, width: {ideal: 1920}, height: {ideal: 1080}}});
      if (current !== generation || !dialog.open || document.hidden) {
        acquired.getTracks().forEach(track => track.stop());
        return;
      }
      stream = acquired;
      video.srcObject = stream;
      video.hidden = false;
      await video.play();
      status.textContent = '제품 라벨의 16자리 ERP 코드를 화면 중앙에 크게 비춰 주세요.';
      let frame = 0;
      async function detect() {
        if (current !== generation || !dialog.open) return;
        try {
          let value;
          if (detector) {
            try { value = (await detector.detect(video)).find(code => code.rawValue)?.rawValue; }
            catch (error) { /* Try local decoding below. */ }
          }
          if (!value && video.readyState >= 2) {
            try { value = decodeImage(video, video.videoWidth, video.videoHeight, 1600, (frame++ % 2) * 90); }
            catch (error) { /* No barcode in this frame; continue scanning. */ }
          }
          if (current !== generation || !dialog.open) return;
          if (value && accept(value)) return;
        } catch (error) {
          if (current === generation) status.textContent = '라벨을 읽지 못했습니다. 각도와 밝기를 조정하거나 직접 입력하세요.';
        }
        if (current === generation) timer = setTimeout(detect, 250);
      }
      detect();
    } catch (error) {
      if (current !== generation) return;
      stop();
      status.textContent = '카메라를 시작하지 못했습니다. 브라우저 카메라 권한을 확인하거나 사진 촬영 / 이미지에서 읽기를 이용하세요.';
    }
  });
})();
