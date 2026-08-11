import os
import sys
import time
import json
import random
import requests
import pymysql
from bs4 import BeautifulSoup
from urllib.parse import urljoin

# ==========================================
# CẤU HÌNH CƠ BẢN
# ==========================================
INSTANCE_ID = os.getenv("INSTANCE_ID", "1")
MAX_RUNTIME_SECONDS = int(os.getenv("MAX_RUNTIME_SECONDS", "3000"))  # Mặc định 50 phút
REQUEST_DELAY_SECONDS = float(os.getenv("REQUEST_DELAY_SECONDS", "7.0"))  # Đảm bảo >= 7s

MYSQL_HOST = os.getenv("MYSQL_HOST", "103.20.97.106")
MYSQL_USER = os.getenv("MYSQL_USER", "thongtin_remote")
MYSQL_PASSWORD = os.getenv("MYSQL_PASSWORD", "Bv$!K~D&@H@t~hQ)")
MYSQL_DATABASE = os.getenv("MYSQL_DATABASE", "thongtindoanhnghiep")

BASE_URL = "https://masothue.com"

# Headers cố định (tránh sinh User-Agent ngẫu nhiên dễ bị chặn)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "vi-VN,vi;q=0.8,en-US;q=0.5,en;q=0.3",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
    "Referer": "https://masothue.com/"
}

# ==========================================
# CƠ SỞ DỮ LIỆU
# ==========================================
def get_mysql_connection():
    return pymysql.connect(
        host=MYSQL_HOST,
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DATABASE,
        charset="utf8mb4",
        autocommit=True
    )

def init_mysql_tables():
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        
        # Bảng lưu kết quả
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS `masothuecanhan` (
              `cccd` varchar(20) NOT NULL PRIMARY KEY,
              `tax_code` varchar(20) DEFAULT NULL,
              `status` varchar(200) DEFAULT NULL,
              `legal_rep` longtext DEFAULT NULL,
              `managed_by` longtext DEFAULT NULL,
              `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        
        # Bảng tiến trình cào (tiền tố 6 số đầu của CCCD)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS `cccd_progress` (
              `prefix` varchar(6) NOT NULL PRIMARY KEY,
              `last_seq` int DEFAULT 0,
              `status` varchar(20) DEFAULT 'IN_PROGRESS',
              `updated_at` TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        """)
        
        conn.close()
    except Exception as e:
        print(f"⚠️ Lỗi khởi tạo bảng MySQL: {e}", flush=True)

def get_and_increment_cccd():
    conn = get_mysql_connection()
    try:
        # Tắt autocommit để dùng transaction lock
        conn.autocommit(False)
        cursor = conn.cursor()
        
        # Tìm một prefix đang chạy dở
        cursor.execute("SELECT prefix, last_seq FROM cccd_progress WHERE status = 'IN_PROGRESS' ORDER BY RAND() LIMIT 1 FOR UPDATE")
        row = cursor.fetchone()
        
        if not row:
            # Tạo ngẫu nhiên một tiền tố CCCD mới
            # Tỉnh: 001 -> 096
            # Giới tính: 0, 1, 2, 3
            # Năm sinh: 00 -> 99
            while True:
                prefix = f"{random.randint(1,96):03d}{random.randint(0,3)}{random.randint(0,99):02d}"
                try:
                    cursor.execute("INSERT INTO cccd_progress (prefix, last_seq, status) VALUES (%s, 0, 'IN_PROGRESS')", (prefix,))
                    break
                except pymysql.err.IntegrityError:
                    # Trùng prefix thì tạo lại
                    continue
            
            cursor.execute("SELECT prefix, last_seq FROM cccd_progress WHERE prefix = %s FOR UPDATE", (prefix,))
            row = cursor.fetchone()
            
        prefix, last_seq = row
        next_seq = last_seq + 1
        
        status = 'IN_PROGRESS'
        if next_seq >= 999999:
            status = 'DONE'
            
        cursor.execute("UPDATE cccd_progress SET last_seq = %s, status = %s WHERE prefix = %s", (next_seq, status, prefix))
        conn.commit()
        return f"{prefix}{next_seq:06d}"
    except Exception as e:
        conn.rollback()
        print(f"⚠️ Lỗi khi sinh CCCD: {e}", flush=True)
        time.sleep(2)
        return None
    finally:
        conn.close()

def save_result(cccd, tax_code, status, legal_rep, managed_by):
    try:
        conn = get_mysql_connection()
        cursor = conn.cursor()
        sql = """
            INSERT INTO masothuecanhan (cccd, tax_code, status, legal_rep, managed_by)
            VALUES (%s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                tax_code = VALUES(tax_code),
                status = VALUES(status),
                legal_rep = VALUES(legal_rep),
                managed_by = VALUES(managed_by)
        """
        cursor.execute(sql, (cccd, tax_code, status, legal_rep, managed_by))
        conn.close()
    except Exception as e:
        print(f"⚠️ Lỗi lưu CSDL cho CCCD {cccd}: {e}", flush=True)

# ==========================================
# CRAWLER LOGIC
# ==========================================

def handle_cloudflare_block():
    print(f"\n[!] Bị Cloudflare chặn (403/429). Ngủ 60s rồi khởi động lại Github Action...", flush=True)
    time.sleep(60)
    
    # Github Action (Step 5) đã có sẵn logic tự mở lại khi thoát sys.exit(0)
    print(f"[!] Thoát tiến trình để khởi động lại máy ảo.", flush=True)
    sys.exit(0)

def fetch_content(url):
    try:
        res = requests.get(url, headers=HEADERS, timeout=15)
        if res.status_code in [403, 429]:
            handle_cloudflare_block()
            
        if res.status_code == 200:
            return res.text
        
        print(f"⚠️ [HTTP {res.status_code}] Khi truy cập {url}", flush=True)
        return None
    except requests.exceptions.RequestException as e:
        print(f"⚠️ Lỗi mạng khi truy cập {url}: {e}", flush=True)
        return None

def parse_detail_page(html, cccd):
    soup = BeautifulSoup(html, 'lxml')
    
    def get_val(label):
        td = soup.find(lambda tag: tag.name == "td" and label in tag.text)
        if td and td.find_next_sibling("td"):
            # Xóa các nút bấm hoặc script rác trong cột giá trị
            for b in td.find_next_sibling("td").find_all(['button', 'script', 'style']): 
                b.decompose()
            val = td.find_next_sibling("td").get_text(separator=" ", strip=True)
            return " ".join(val.split()) if val else "Không có"
        return "Không có"
        
    tax_code = get_val("Mã số thuế cá nhân")
    if tax_code == "Không có":
        # Không tìm thấy thông tin trên trang này
        return False
        
    status = get_val("Tình trạng")
    legal_rep = get_val("Người đại diện")
    managed_by = get_val("Quản lý bởi")
    
    save_result(cccd, tax_code, status, legal_rep, managed_by)
    print(f"  [+] Đã lưu: {tax_code} - {legal_rep} ({status})")
    return True

def process_cccd(cccd):
    search_url = f"{BASE_URL}/Search/?q={cccd}&type=auto"
    html = fetch_content(search_url)
    if not html:
        return
        
    soup = BeautifulSoup(html, 'lxml')
    links = set()
    # Tìm các link chi tiết trong danh sách kết quả tìm kiếm
    for div in soup.find_all("div", class_="tax-listing"):
        for a in div.find_all("a", href=True):
            href = a['href']
            # Chỉ lấy các đường dẫn bắt đầu bằng / (link nội bộ)
            if href.startswith("/") and "Search" not in href:
                links.add(urljoin(BASE_URL, href))
                
    if not links:
        # Nếu search trả thẳng về trang chi tiết (do masothue.com tự redirect nếu có 1 kết quả duy nhất)
        if soup.find("td", string=lambda s: s and "Mã số thuế cá nhân" in s):
            parse_detail_page(html, cccd)
        else:
            print(f"[-] {cccd}: Không có kết quả.")
        return

    # Nếu có danh sách kết quả, vào từng link bóc dữ liệu
    print(f"[*] {cccd}: Tìm thấy {len(links)} kết quả.")
    for link in links:
        # Giãn cách các request chi tiết
        time.sleep(REQUEST_DELAY_SECONDS)
        
        detail_html = fetch_content(link)
        if detail_html:
            parse_detail_page(detail_html, cccd)

# ==========================================
# THIẾT LẬP VÒNG LẶP CHÍNH
# ==========================================
def main():
    start_time = time.time()
    print(f"==================================================", flush=True)
    print(f"🚀 CRAWLER MÃ SỐ THUẾ CÁ NHÂN #{INSTANCE_ID} KHỞI CHẠY", flush=True)
    print(f"⏱️ Thời gian tối đa: {MAX_RUNTIME_SECONDS} giây ({MAX_RUNTIME_SECONDS/60:.1f} phút)", flush=True)
    print(f"🐢 Giãn cách ít nhất {REQUEST_DELAY_SECONDS}s / request", flush=True)
    print(f"==================================================", flush=True)

    init_mysql_tables()

    count = 0
    while True:
        elapsed = time.time() - start_time
        if elapsed > MAX_RUNTIME_SECONDS:
            print(f"⏱️ Đã chạy {elapsed:.1f}s. Hết giờ. Tự động thoát...", flush=True)
            sys.exit(0)  # Thoát 0 để Github Action gọi luồng tiếp theo
            
        cccd = get_and_increment_cccd()
        if not cccd:
            time.sleep(1)
            continue
            
        print(f"-> Đang tìm kiếm CCCD: {cccd}", flush=True)
        process_cccd(cccd)
        count += 1
        
        # Giãn cách mỗi lần tìm kiếm CCCD
        time.sleep(REQUEST_DELAY_SECONDS)

if __name__ == "__main__":
    main()
