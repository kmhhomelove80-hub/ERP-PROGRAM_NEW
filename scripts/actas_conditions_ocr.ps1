param([Parameter(Mandatory=$true)][string]$Image, [ValidateSet('ko-KR','en-US')][string]$Language='ko-KR')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
try {
    Add-Type -AssemblyName System.Runtime.WindowsRuntime
    $null=[Windows.Storage.StorageFile,Windows.Storage,ContentType=WindowsRuntime]
    $null=[Windows.Storage.Streams.IRandomAccessStream,Windows.Storage.Streams,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.BitmapDecoder,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Graphics.Imaging.SoftwareBitmap,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Media.Ocr.OcrEngine,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Media.Ocr.OcrResult,Windows.Foundation,ContentType=WindowsRuntime]
    $null=[Windows.Globalization.Language,Windows.Globalization,ContentType=WindowsRuntime]
    $languageObject=[Windows.Globalization.Language]::new($Language)
    if(-not [Windows.Media.Ocr.OcrEngine]::IsLanguageSupported($languageObject)) {
        @{status='language_unavailable';language=$Language}|ConvertTo-Json -Compress
        exit 0
    }
    $engine=[Windows.Media.Ocr.OcrEngine]::TryCreateFromLanguage($languageObject)
    if($null -eq $engine){throw 'No local OCR engine'}
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
        $bitmap=Await-Ocr ($decoder.GetSoftwareBitmapAsync()) ([Windows.Graphics.Imaging.SoftwareBitmap])
        try {
            if($bitmap.PixelWidth -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension -or $bitmap.PixelHeight -gt [Windows.Media.Ocr.OcrEngine]::MaxImageDimension){throw 'Condition image exceeds local OCR dimensions'}
            $result=Await-Ocr ($engine.RecognizeAsync($bitmap)) ([Windows.Media.Ocr.OcrResult])
            $lines=@($result.Lines|ForEach-Object {
                $line=$_
                @{text=$line.Text;words=@($line.Words|ForEach-Object {
                    @{text=$_.Text;x=$_.BoundingRect.X;y=$_.BoundingRect.Y;width=$_.BoundingRect.Width;height=$_.BoundingRect.Height}
                })}
            })
            @{status='ok';language=$Language;width=$bitmap.PixelWidth;height=$bitmap.PixelHeight;lines=$lines}|ConvertTo-Json -Depth 8 -Compress
        } finally {$bitmap.Dispose()}
    } finally {$stream.Dispose()}
} catch {
    # Never print image text, machine paths or runtime connection details on failure.
    @{status='error';failure_type=$_.Exception.GetType().Name}|ConvertTo-Json -Compress
    exit 1
}
