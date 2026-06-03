# Cinebudget Deploy Script
# Usage: .\deploy.ps1

$KEY = "C:\Users\Becket Nelson\OneDrive\Claude Code Stuff\cinebudget-key.pem"
$SERVER = "3.21.29.86"

Write-Host "Deploying to EC2..." -ForegroundColor Cyan

ssh -i $KEY ubuntu@$SERVER "cd ~/Cinebudget && git pull && sudo systemctl restart cinebudget && sleep 3 && sudo systemctl status cinebudget --no-pager | head -5"

Write-Host "Done. Check http://$SERVER:5000" -ForegroundColor Green
