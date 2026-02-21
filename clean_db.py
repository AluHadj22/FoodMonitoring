# clean_db.py
from database import SessionLocal
import models

def clean_database():
    db = SessionLocal()
    try:
        # Удаляем все элементы дашбордов
        db.query(models.DashboardElement).delete()
        # Удаляем все дашборды
        db.query(models.Dashboard).delete()
        db.commit()
        print("✅ База данных очищена от дашбордов")
    except Exception as e:
        print(f"❌ Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    clean_database()