# check_dashboards.py
from database import SessionLocal
import models
import json

def check_dashboards():
    db = SessionLocal()
    try:
        # Проверяем все дашборды
        dashboards = db.query(models.Dashboard).all()
        print(f"Всего дашбордов в БД: {len(dashboards)}")
        
        for d in dashboards:
            print(f"\nID: {d.id}")
            print(f"Название: {d.title}")
            print(f"Slug: {d.slug}")
            print(f"Опубликован: {d.is_published}")
            print(f"Создан: {d.created_at}")
            print(f"Обновлен: {d.updated_at}")
            
            # Проверяем элементы
            elements = db.query(models.DashboardElement).filter(
                models.DashboardElement.dashboard_id == d.id
            ).all()
            print(f"Элементов: {len(elements)}")
            
    except Exception as e:
        print(f"Ошибка: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    check_dashboards()