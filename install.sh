#!/bin/bash
echo "[*] Memulai Instalasi Kebutuhan Sistem & Python..."

# 1. Update dan install aplikasi sistem operasi
sudo apt update
sudo apt install -y hostapd dnsmasq iptables net-tools psmisc python3-pip

# 2. Hentikan service agar tidak bentrok saat instalasi awal
sudo systemctl stop hostapd
sudo systemctl stop dnsmasq
sudo systemctl disable hostapd
sudo systemctl disable dnsmasq

# 3. Install modul Python dari requirements.txt
# (Gunakan --break-system-packages jika di Kali Linux terbaru)
pip3 install -r requirements.txt --break-system-packages

echo "[+] Instalasi Selesai! Anda sekarang bisa menjalankan: sudo python3 portal.py"
