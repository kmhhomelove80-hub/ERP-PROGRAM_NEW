/* Supported by Binary Eye's official README and AndroidManifest.xml.
 * https://github.com/markusfisch/BinaryEye#deep-links
 * No arbitrary scanner URI or browser-to-native result API is assumed.
 */
(() => {
  const button = document.querySelector('#scan-open');
  const dialog = document.querySelector('#scan-dialog');
  const link = document.querySelector('#binaryeye-launch');
  const status = document.querySelector('#scan-status');
  if (!button || !dialog || !link) return;
  const android = /Android/i.test(navigator.userAgent);
  const returnURL = new URL('/', window.location.origin);
  returnURL.search = '?scan=1&content={RESULT}';
  const launchURL = 'binaryeye://scan?ret=' + encodeURIComponent(returnURL.href);
  link.href = launchURL;
  link.hidden = !android;
  document.querySelector('#scan-close').addEventListener('click', () => dialog.close());
  button.addEventListener('click', () => {
    dialog.showModal();
    status.textContent = android
      ? 'Binary Eye를 실행합니다. 열리지 않으면 앱 설치·업데이트 후 아래 실행 버튼을 누르거나, 앱을 직접 열어 스캔하세요.'
      : 'Binary Eye는 Android 앱입니다. 이 기기에서는 검색창에 ERP 코드를 직접 입력하거나 바코드 스캐너 키보드 입력을 사용하세요.';
    // Keep the ERP guidance visible if the browser cannot resolve the app.
    // A direct user gesture opens the supported URI in a separate context.
    if (android) window.open(launchURL, '_blank', 'noopener');
  });
})();
