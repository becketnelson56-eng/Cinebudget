# Cinebudget Deploy Script
# Usage: .\deploy.ps1
# Pulls latest code from GitHub on EC2 and restarts the service

$KEY = "C:\Users\Becket Nelson\OneDrive\Claude Code Stuff\cinebudget-key.pem"
$HOST = "YOUR_ELASTIC_IP"  # Replace with your actual Elastic IP

Write-Host "Deploying to EC2..." -ForegroundColor Cyan

ssh -i $KEY ubuntu@$HOST @"
cd ~/Cinebudget
git pull
sudo swapon /swapfile 2>/dev/null || true
sudo systemctl restart cinebudget
sleep 3
sudo systemctl status cinebudget --no-pager | head -5
"@

Write-Host "Done. Check http://$HOST`:5000" -ForegroundColor Green
