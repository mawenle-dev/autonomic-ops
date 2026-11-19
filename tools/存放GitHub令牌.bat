@echo off
chcp 65001 >nul
cd /d "%~dp0"
if not exist ".secrets" mkdir ".secrets"
echo ============================================================
echo  GitHub 令牌放置工具
echo ============================================================
echo  接下来会打开记事本。
echo  1. 把令牌整段粘贴进去（github_pat_ 开头，只一行）
echo  2. 按 Ctrl+S 保存，然后关闭记事本
echo  3. 保存位置固定为：%CD%\.secrets\github_token
echo ============================================================
pause
start "" notepad ".secrets\github_token"
