@echo off
chcp 65001 >nul
echo ================================
echo  HTTP 代理服务器启动脚本
echo ================================
echo.

REM 检查虚拟环境是否存在
if not exist "venv\Scripts\activate.bat" (
    echo [信息] 未找到虚拟环境，正在创建...
    python -m venv venv
    if errorlevel 1 (
        echo [错误] 创建虚拟环境失败
        pause
        exit /b 1
    )
    echo [成功] 虚拟环境创建完成
    echo.
)

REM 激活虚拟环境
echo [信息] 激活虚拟环境...
call venv\Scripts\activate.bat

REM 检查依赖是否安装
echo [信息] 检查依赖...
pip show requests >nul 2>&1
if errorlevel 1 (
    echo [信息] 安装依赖包...
    pip install -r requirements.txt
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
    echo [成功] 依赖安装完成
    echo.
)

REM 启动代理服务器
echo [信息] 启动代理服务器...
echo.
python proxy_server.py

REM 如果服务器退出
echo.
echo [信息] 服务器已停止
pause
