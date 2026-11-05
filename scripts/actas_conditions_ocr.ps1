param([Parameter(Mandatory=$true)][string]$Image, [ValidateSet('ko-KR','en-US')][string]$Language='ko-KR')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
    $null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.BitmapTransform,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.BitmapBounds,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
    $languageObject=[Windows.Globalization.Language]::new($Language)
    $supported=[Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($languageObject)
    $engine=$null
    if($supported){$engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languageObject)}
    $script:asTask=[System.WindowsRuntimeSystemExtensions].GetMethods()|Where-Object {
        $_.Name -eq 'AsTask' -and $_.IsGenericMethod -and $_.GetParameters().Count -eq 1 -and
        $_.GetParameters()[0].ParameterType.Name -eq 'IAsyncOperation`1'
    }|Select-Object -First 1
    function Await-Ocr($Operation,[Type]$ResultType) {
        $task=$script:asTask.MakeGenericMethod($ResultType).Invoke($null,@($Operation))
        if(-not $task.Wait(10000)){throw 'Local OCR operation timed out'}
        return $task.Result
    }
    $file=Await-Ocr ([Windows.Storage.StorageFile]::GetFileFromPathAsync($Image)) ([Windows.Storage.StorageFile])
    $stream=Await-Ocr ($file.OpenAsync([Windows.Storage.FileAccessMode]::Read)) ([Windows.Storage.Streams.IRandomAccessStream])
    try {
        $decoder=Await-Ocr ([Windows.Graphics.Imaging.BitmapDecoder]::CreateAsync($stream)) ([Windows.Graphics.Imaging.BitmapDecoder])
        # Source text is only about ten pixels high. Scale first, then crop in
        # the scaled coordinate space; report word boxes in ORIGINAL pixels.
        $scale=3
        $cropWidth=[Math]::Min(400,[int]$decoder.PixelWidth)
        $transform=[Windows.Graphics.Imaging.BitmapTransform]::new()
        $transform.ScaledWidth=[uint32]($decoder.PixelWidth*$scale)
        $transform.ScaledHeight=[uint32]($decoder.PixelHeight*$scale)
        $bounds=[Windows.Graphics.Imaging.BitmapBounds]::new()
        $bounds.X=0;$bounds.Y=0
        $bounds.Width=[uint32]($cropWidth*$scale);$bounds.Height=[uint32]($decoder.PixelHeight*$scale)
        $transform.Bounds=$bounds
        $bitmap=Await-Ocr ($decoder.GetSoftwareBitmapAsync([Windows.Graphics.Imaging.BitmapPixelFormat]::Bgra8,[Windows.Graphics.Imaging.BitmapAlphaMode]::Ignore,$transform,[Windows.Graphics.Imaging.ExifOrientationMode]::IgnoreExifOrientation,[Windows.Graphics.Imaging.ColorManagementMode]::DoNotColorManage)) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            if($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension){throw 'Condition image exceeds local OCR dimensions'}
            if(-not $supported -or $null -eq $engine) {
                @{status='language_unavailable';language=$Language;width=$decoder.PixelWidth;height=$decoder.PixelHeight;scale=$scale;processed_width=$bitmap.PixelWidth;processed_height=$bitmap.PixelHeight}|ConvertTo-Json -Compress
                exit 0
            }
            $result=Await-Ocr ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $lines=@($result.Lines|ForEach-Object {
                $line=$_
                @{text=$line.Text;words=@($line.Words|ForEach-Object {
                    @{text=$_.Text;x=$_.BoundingRect.X/$scale;y=$_.BoundingRect.Y/$scale;width=$_.BoundingRect.Width/$scale;height=$_.BoundingRect.Height/$scale}
                })}
            })
            @{status='ok';language=$Language;width=$decoder.PixelWidth;height=$decoder.PixelHeight;crop_width=$cropWidth;scale=$scale;processed_width=$bitmap.PixelWidth;processed_height=$bitmap.PixelHeight;lines=$lines}|ConvertTo-Json -Depth 8 -Compress
        } finally {$bitmap.Dispose()}
    } finally {$stream.Dispose()}
} catch {
    # Never print image text, machine paths or runtime connection details on failure.
    @{status='error';failure_type=$_.Exception.GetType().Name}|ConvertTo-Json -Compress
    exit 1
}
