# 📡 Wi-Fi Captive Portal Security Lab

Ini adalah tools simulasi *Captive Portal / Rogue Access Point* berbasis Python dan Flask. Dibuat khusus untuk keperluan edukasi dan *Security Awareness Training* guna memonitor perangkat yang terhubung ke jaringan terbuka.

## 🚀 Fitur Utama
* All-in-One script (Hostapd + Dnsmasq + Iptables + Flask).
* Real-time Dashboard Monitoring.
* OS & Device Fingerprinting (HP vs Laptop, MAC Fisik vs Acak).
* Sistem Penilaian Skor Risiko (Merah, Kuning, Hijau).
* Export laporan ke format CSV.

## 🛠️ Instalasi & Penggunaan
Hanya berfungsi di sistem operasi berbasis Linux (direkomendasikan Kali Linux / Ubuntu).
```bash
git clone [https://github.com/username-anda/nama-repo.git](https://github.com/username-anda/nama-repo.git)
cd nama-repo
chmod +x install.sh
./install.sh
sudo python3 portal.py --ssid "nama_ssid"
