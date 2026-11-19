param (
    [string]$pptxPath = "C:\Praveen\lipsync\Skill-Bridge\Backend\uploads\courses\52365db9-5ac1-46b4-b445-250c5a900e9b\Safety Visitor Brief_Rev.pptx",
    [string]$outputDir = "C:\Praveen\lipsync\Skill-Bridge\Backend\uploads\courses\52365db9-5ac1-46b4-b445-250c5a900e9b\slides"
)

if (-not (Test-Path $pptxPath)) {
    Write-Error "PPTX file not found: $pptxPath"
    exit 1
}

if (-not (Test-Path $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}

try {
    Write-Host "Starting PowerPoint COM..."
    $ppt = New-Object -ComObject PowerPoint.Application
    # Open presentation hidden/read-only: ReadOnly=True, Untitled=False, WithWindow=False
    $pres = $ppt.Presentations.Open($pptxPath, [Microsoft.Office.Core.MsoTriState]::msoTrue, [Microsoft.Office.Core.MsoTriState]::msoFalse, [Microsoft.Office.Core.MsoTriState]::msoFalse)
    $total = $pres.Slides.Count
    Write-Host "Presentation opened! Total slides: $total"

    for ($i = 1; $i -le $total; $i++) {
        $outFile1 = Join-Path $outputDir ("Slide" + $i + ".png")
        $outFile2 = Join-Path $outputDir ("slide_" + $i + ".png")
        $pres.Slides.Item($i).Export($outFile1, "PNG", 1920, 1080)
        Copy-Item -Path $outFile1 -Destination $outFile2 -Force
        Write-Host "Exported slide $i to $outFile1"
    }

    $pres.Close()
    $ppt.Quit()
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($pres) | Out-Null
    [System.Runtime.Interopservices.Marshal]::ReleaseComObject($ppt) | Out-Null
    [System.GC]::Collect()
    [System.GC]::WaitForPendingFinalizers()
    Write-Host "All slides exported successfully!"
} catch {
    Write-Error "PowerPoint export failed: $_"
    exit 1
}
