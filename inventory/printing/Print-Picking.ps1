param(
    [Parameter(Mandatory=$true)][string]$PayloadPath,
    [string]$PreviewDirectory,
    [switch]$ValidateOnly
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object System.Text.UTF8Encoding($false)
Add-Type -AssemblyName System.Drawing
$job = Get-Content -LiteralPath $PayloadPath -Raw -Encoding UTF8 | ConvertFrom-Json
if (-not $job.document.rows -or $job.document.rows.Count -gt 10000) { throw '피킹 행 수를 확인하세요.' }
if ([int]$job.copies -lt 1 -or [int]$job.copies -gt 5) { throw '부수는 1~5부입니다.' }
$script:rowIndex = 0
$script:lineOffset = 0
$script:pageNumber = 0
$script:cachedLines = $null
$font = New-Object System.Drawing.Font('Malgun Gothic', 14, [System.Drawing.FontStyle]::Regular, [System.Drawing.GraphicsUnit]::Pixel)
$bold = New-Object System.Drawing.Font('Malgun Gothic', 14, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$titleFont = New-Object System.Drawing.Font('Malgun Gothic', 21, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
$pen = New-Object System.Drawing.Pen([System.Drawing.Color]::FromArgb(180,190,205), 0.7)
$brush = New-Object System.Drawing.SolidBrush([System.Drawing.Color]::FromArgb(20,43,73))
function Split-Cell([System.Drawing.Graphics]$g, [string]$text, [float]$width) {
    $result = New-Object 'System.Collections.Generic.List[string]'
    $line = ''
    foreach ($c in ($text -replace '[\x00-\x08\x0B\x0C\x0E-\x1F]', '').ToCharArray()) {
        if ($c -eq "`n") { $result.Add($line); $line=''; continue }
        if ($c -eq "`r") { continue }
        $candidate = $line + $c
        if ($line.Length -gt 0 -and $g.MeasureString($candidate,$font).Width -gt $width) { $result.Add($line); $line=[string]$c } else { $line=$candidate }
    }
    $result.Add($line)
    return ,$result.ToArray()
}
function Draw-Page([System.Drawing.Graphics]$g,[System.Drawing.RectangleF]$bounds) {
    $script:pageNumber++
    $g.PageUnit=[System.Drawing.GraphicsUnit]::Pixel
    $g.ScaleTransform(($g.DpiX/100.0),($g.DpiY/100.0))
    $x=[float]$bounds.X; $y=[float]$bounds.Y; $width=[float]$bounds.Width
    $g.DrawString('피킹 목록',$titleFont,[System.Drawing.Brushes]::Black,$x,$y)
    $y+=31
    $g.DrawString(([string]$job.document.number+' · '+[string]$job.document.registered+' · 총수량 '+[string]$job.document.total),$font,[System.Drawing.Brushes]::Black,$x,$y)
    $y+=24
    $projectLines=Split-Cell $g ([string]$job.document.project) ($width-10)
    foreach($line in $projectLines){$g.DrawString($line,$bold,[System.Drawing.Brushes]::Black,$x,$y);$y+=18}
    $headers=@('ERP 코드','모델 / 사양','위치','수량','입고 / 처리 상태')
    $ratios=@(0.18,0.36,0.13,0.08,0.25)
    $widths=@($ratios|ForEach-Object {[float]($_*$width)})
    $left=$x
    for($i=0;$i -lt 5;$i++){
        $g.FillRectangle($brush,$left,$y,$widths[$i],27)
        $g.DrawString($headers[$i],$bold,[System.Drawing.Brushes]::White,($left+4),($y+4))
        $left+=$widths[$i]
    }
    $y+=27; $bottom=$bounds.Bottom-30
    while($script:rowIndex -lt $job.document.rows.Count){
        $r=$job.document.rows[$script:rowIndex]
        if($null -eq $script:cachedLines){
            $texts=@([string]$r.code,[string]$r.description,[string]$r.location,[string]$r.quantity,([string]$r.receipt+' / '+[string]$r.status))
            $script:cachedLines=New-Object 'System.Collections.Generic.List[object]'
            for($i=0;$i -lt 5;$i++){ $script:cachedLines.Add((Split-Cell $g $texts[$i] ($widths[$i]-10))) }
        }
        $lineCount=1
        foreach($lines in $script:cachedLines){$lineCount=[Math]::Max($lineCount,$lines.Length)}
        $available=[int][Math]::Floor(($bottom-$y-10)/18)
        if($available -lt 1){break}
        $take=[Math]::Min($available,$lineCount-$script:lineOffset)
        $height=$take*18+10; $left=$x
        for($i=0;$i -lt 5;$i++){
            $g.DrawRectangle($pen,$left,$y,$widths[$i],$height)
            $lines=$script:cachedLines[$i]
            for($j=0;$j -lt $take;$j++){
                $index=$script:lineOffset+$j
                if($index -lt $lines.Length){$g.DrawString([string]$lines[$index],$font,[System.Drawing.Brushes]::Black,($left+4),($y+5+$j*18))}
            }
            $left+=$widths[$i]
        }
        $y+=$height; $script:lineOffset+=$take
        if($script:lineOffset -ge $lineCount){$script:rowIndex++;$script:lineOffset=0;$script:cachedLines=$null}
        if($y -ge $bottom-28){break}
    }
    $g.DrawString(('인쇄 요청 '+[string]$job.job_id+' · '+$script:pageNumber+'쪽'),$font,[System.Drawing.Brushes]::Gray,$x,($bounds.Bottom-22))
    return ($script:rowIndex -lt $job.document.rows.Count)
}
try {
    if($PreviewDirectory){
        [void](New-Item -ItemType Directory -Path $PreviewDirectory -Force)
        do{
            $bitmap=New-Object System.Drawing.Bitmap(1169,827)
            $bitmap.SetResolution(100,100)
            $g=[System.Drawing.Graphics]::FromImage($bitmap)
            try{
                $g.Clear([System.Drawing.Color]::White)
                $again=Draw-Page $g (New-Object System.Drawing.RectangleF(40,40,1089,747))
                $bitmap.Save((Join-Path $PreviewDirectory ('page-'+$script:pageNumber+'.png')),[System.Drawing.Imaging.ImageFormat]::Png)
            }finally{$g.Dispose();$bitmap.Dispose()}
            if($script:pageNumber -gt 10000){throw '인쇄 페이지 제한을 초과했습니다.'}
        }while($again)
        @{state='preview';pages=$script:pageNumber;rows=$script:rowIndex}|ConvertTo-Json -Compress
    }else{
        $doc=New-Object System.Drawing.Printing.PrintDocument
        try{
            $doc.PrinterSettings.PrinterName=[string]$job.printer
            if(-not $doc.PrinterSettings.IsValid){throw '해당 계정에서 프린터를 사용할 수 없습니다.'}
            $doc.PrinterSettings.Copies=[int16]$job.copies
            $doc.DefaultPageSettings.Landscape=$true
            $doc.DefaultPageSettings.Margins=New-Object System.Drawing.Printing.Margins(40,40,40,40)
            foreach($paper in $doc.PrinterSettings.PaperSizes){if($paper.Kind -eq [System.Drawing.Printing.PaperKind]::A4){$doc.DefaultPageSettings.PaperSize=$paper;break}}
            $doc.DocumentName='SUNIL Picking '+[string]$job.document.number+' '+[string]$job.job_id
            $doc.PrintController=New-Object System.Drawing.Printing.StandardPrintController
            $doc.add_PrintPage({param($sender,$e) $e.HasMorePages=Draw-Page $e.Graphics ([System.Drawing.RectangleF]$e.MarginBounds)})
            if($ValidateOnly){@{state='validated';printer=$doc.PrinterSettings.PrinterName}|ConvertTo-Json -Compress}
            else{$doc.Print();@{state='spooler_submitted';pages=$script:pageNumber}|ConvertTo-Json -Compress}
        }finally{$doc.Dispose()}
    }
}finally{$font.Dispose();$bold.Dispose();$titleFont.Dispose();$pen.Dispose();$brush.Dispose()}
