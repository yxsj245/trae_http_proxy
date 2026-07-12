<#
.SYNOPSIS
    HTTP 代理服务器管理脚本
.DESCRIPTION
    支持两种模式：
    1. run - 启动代理服务器（默认）
    2. build - 打包为可执行文件
.PARAMETER Mode
    运行模式：run 或 build
.EXAMPLE
    .\start.ps1
    .\start.ps1 run
    .\start.ps1 build
#>

param(
    [Parameter(Position=0)]
    [ValidateSet("run", "build")]
    [string]$Mode = "run"
)

# 设置控制台编码为 UTF-8
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8

Write-Host "================================" -ForegroundColor Cyan
Write-Host " HTTP 代理服务器管理脚本" -ForegroundColor Cyan
Write-Host "================================" -ForegroundColor Cyan
Write-Host ""

# 检查 Python 是否安装
try {
    $pythonVersion = python --version 2>&1
    Write-Host "[信息] 检测到 Python: $pythonVersion" -ForegroundColor Green
} catch {
    Write-Host "[错误] 未找到 Python，请先安装 Python 3.7 或更高版本" -ForegroundColor Red
    Read-Host "按回车键退出"
    exit 1
}

# 虚拟环境路径
$venvPath = "venv"
$venvPython = Join-Path $venvPath "Scripts\python.exe"
$venvPip = Join-Path $venvPath "Scripts\pip.exe"
$venvActivate = Join-Path $venvPath "Scripts\Activate.ps1"

# 检查并创建虚拟环境
if (-not (Test-Path $venvActivate)) {
    Write-Host "[信息] 未找到虚拟环境，正在创建..." -ForegroundColor Yellow
    python -m venv $venvPath
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 创建虚拟环境失败" -ForegroundColor Red
        Read-Host "按回车键退出"
        exit 1
    }
    Write-Host "[成功] 虚拟环境创建完成" -ForegroundColor Green
    Write-Host ""
}

# 激活虚拟环境
Write-Host "[信息] 激活虚拟环境..." -ForegroundColor Yellow
& $venvActivate

# 检查依赖是否安装
Write-Host "[信息] 检查依赖..." -ForegroundColor Yellow
$requestsInstalled = & $venvPip show requests 2>$null
if (-not $requestsInstalled) {
    Write-Host "[信息] 安装依赖包..." -ForegroundColor Yellow
    & $venvPip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) {
        Write-Host "[错误] 依赖安装失败" -ForegroundColor Red
        Read-Host "按回车键退出"
        exit 1
    }
    Write-Host "[成功] 依赖安装完成" -ForegroundColor Green
    Write-Host ""
}

# 根据模式执行不同操作
switch ($Mode) {
    "run" {
        Write-Host "[信息] 启动代理服务器..." -ForegroundColor Yellow
        Write-Host ""
        & $venvPython proxy_server.py
        
        Write-Host ""
        Write-Host "[信息] 服务器已停止" -ForegroundColor Yellow
    }
    
    "build" {
        Write-Host "[信息] 开始打包为可执行文件..." -ForegroundColor Yellow
        Write-Host ""
        
        # 检查是否安装 PyInstaller
        $pyinstallerInstalled = & $venvPip show pyinstaller 2>$null
        if (-not $pyinstallerInstalled) {
            Write-Host "[信息] 安装 PyInstaller..." -ForegroundColor Yellow
            & $venvPip install pyinstaller
            if ($LASTEXITCODE -ne 0) {
                Write-Host "[错误] PyInstaller 安装失败" -ForegroundColor Red
                Read-Host "按回车键退出"
                exit 1
            }
            Write-Host "[成功] PyInstaller 安装完成" -ForegroundColor Green
            Write-Host ""
        }
        
        # 创建打包输出目录
        $distPath = "dist"
        if (Test-Path $distPath) {
            Write-Host "[信息] 清理旧的打包文件..." -ForegroundColor Yellow
            Remove-Item -Path $distPath -Recurse -Force
        }
        
        # 执行打包
        Write-Host "[信息] 正在打包 proxy_server.py..." -ForegroundColor Yellow
        & $venvPython -m PyInstaller `
            --onefile `
            --name "http_proxy" `
            --add-data "config.yaml;." `
            --clean `
            --console `
            proxy_server.py
        
        if ($LASTEXITCODE -ne 0) {
            Write-Host "[错误] 打包失败" -ForegroundColor Red
            Read-Host "按回车键退出"
            exit 1
        }
        
        Write-Host ""
        Write-Host "[成功] 打包完成！" -ForegroundColor Green
        Write-Host "[信息] 可执行文件位置: $distPath\http_proxy.exe" -ForegroundColor Cyan
        Write-Host "[提示] 运行时需要将 config.yaml 放在与 exe 相同的目录下" -ForegroundColor Yellow
    }
}

Write-Host ""
Read-Host "按回车键退出"
