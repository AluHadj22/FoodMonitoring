import os
import asyncio
import aiosmtplib
from email.mime.text import MIMEText
from dotenv import load_dotenv  # Добавляем импорт

# Явно загружаем .env
load_dotenv()

async def test_smtp():
    # Получаем значения из .env
    username = os.getenv("SMTP_USERNAME")
    password = os.getenv("SMTP_PASSWORD")
    
    # Проверка: загружены ли переменные
    if not username:
        print("❌ Ошибка: SMTP_USERNAME не загружен из .env")
        return
    if not password:
        print("❌ Ошибка: SMTP_PASSWORD не загружен из .env")
        return

    smtp = aiosmtplib.SMTP(
        hostname="smtp.yandex.ru",
        port=465,
        username=username,
        password=password,
        use_tls=True,
        start_tls=False
    )
    
    try:
        await smtp.connect()
        
        # Создаём сообщение
        message = MIMEText("Тест SMTP от ecmpservice@yandex.ru", "plain", "utf-8")
        message["Subject"] = "Тест SMTP"
        
        # Явно задаём From и To
        message["From"] = username  # Теперь точно строка!
        message["To"] = "alu.hadjiev22@yandex.ru"  # Ваш адрес получателя
        
        
        await smtp.send_message(message)
        print("✅ Письмо отправлено успешно!")
        
    except Exception as e:
        print(f"❌ Ошибка: {type(e).__name__}: {e}")

if __name__ == "__main__":
    asyncio.run(test_smtp())
