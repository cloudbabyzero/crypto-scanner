import sys
import json
import gspread
from google.oauth2.service_account import Credentials
import os

def fetch_log_all(spreadsheet_name="Crypto Scanner Dashboard", sheet_name="Trades"):
    try:
        scopes = [
            "https://www.googleapis.com/auth/spreadsheets.readonly",
            "https://www.googleapis.com/auth/drive.readonly"
        ]
        
        # ค้นหาพิกัดไฟล์ json อัตโนมัติในโฟลเดอร์โปรเจกต์
        current_dir = os.path.dirname(os.path.abspath(__file__))
        json_path = os.path.join(current_dir, 'service_account.json')
        
        creds = Credentials.from_service_account_file(json_path, scopes=scopes)
        gc = gspread.authorize(creds)
        
        # เปิดสเปรดชีตหลักโดยอัตโนมัติ
        sh = gc.open(spreadsheet_name)
        worksheet = sh.worksheet(sheet_name)
        
        # ดึงข้อมูลทั้งหมดส่งกลับไปให้ AI คัดกรอง
        all_records = worksheet.get_all_records()
        
        print(json.dumps(all_records, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"Error: {str(e)}")

if __name__ == "__main__":
    # ค่าเริ่มต้นหลักคือ Crypto Scanner Dashboard
    file_name = "Crypto Scanner Dashboard"
    
    # หากผู้ใช้ระบุชีทย่อยมาในคำสั่ง เช่น python drive_fetcher.py "SHEET_NAME"
    sub_sheet = sys.argv[1] if len(sys.argv) > 1 else "Trades"
    
    fetch_log_all(file_name, sub_sheet)
