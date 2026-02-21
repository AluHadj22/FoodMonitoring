# fix_dashboard.py
from database import SessionLocal
import models
import re

def fix_dashboard():
    db = SessionLocal()
    try:
        # Находим дашборд с ID 1
        dashboard = db.query(models.Dashboard).filter(models.Dashboard.id == 2).first()
        if dashboard:
            # Создаем правильный slug
            slug_base = dashboard.title.lower().replace(' ', '-')
            slug_base = re.sub(r'[^a-z0-9-]', '', slug_base)
            dashboard.slug = slug_base
            
            # Публикуем дашборд
            dashboard.is_published = True
            
            db.commit()
            print(f"✅ Дашборд исправлен:")
            print(f"   Название: {dashboard.title}")
            print(f"   Новый slug: {dashboard.slug}")
            print(f"   Опубликован: {dashboard.is_published}")
        else:
            print("❌ Дашборд с ID 1 не найден")
            
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        db.rollback()
    finally:
        db.close()

if __name__ == "__main__":
    fix_dashboard()