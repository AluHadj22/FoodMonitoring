from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), unique=True, index=True)  # Явно задаём длину
    hashed_password = Column(String(255))  # Явно задаём длину
    role = Column(String(50), default="user")  # user, municipal_admin, regional_admin

    unit_name = Column(String(200), nullable=True)  # Название школы (до 200 символов)
    director_name = Column(String(100), nullable=True)  # ФИО директора (до 100 символов)

    district = Column(String(100), nullable=True)  # Район (опционально)
    food_type = Column(String(50), nullable=True)  # Тип питания (опционально)
    url_1c = Column(
        String(255),
        default="https://cemon.ru/MSHP/ru/",
        nullable=True  # Ссылка на 1С (может быть пустой)
    )
    
    # Связь с дашбордами
    dashboards = relationship("Dashboard", back_populates="creator")

class Dashboard(Base):
    __tablename__ = "dashboards"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(255), nullable=False)
    description = Column(Text)
    slug = Column(String(100), unique=True, index=True, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_published = Column(Boolean, default=False)
    created_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    layout_data = Column(Text, default="{}")  # JSON с расположением элементов
    theme = Column(String(50), default="light")
    
    # Связи
    creator = relationship("User", back_populates="dashboards")
    elements = relationship("DashboardElement", back_populates="dashboard", cascade="all, delete-orphan")

class DashboardElement(Base):
    __tablename__ = "dashboard_elements"
    
    id = Column(Integer, primary_key=True, index=True)
    dashboard_id = Column(Integer, ForeignKey("dashboards.id", ondelete="CASCADE"), nullable=False)
    element_type = Column(String(50), nullable=False)  # chart, text, list, table
    chart_type = Column(String(50))  # line, bar, pie, doughnut
    title = Column(String(255))
    content = Column(Text, default="{}")  # JSON с данными
    settings = Column(Text, default="{}")  # JSON с настройками (цвета, размеры)
    position_x = Column(Integer, default=0)
    position_y = Column(Integer, default=0)
    width = Column(Integer, default=4)  # в условных единицах сетки
    height = Column(Integer, default=4)
    order_index = Column(Integer, default=0)
    
    # Связи
    dashboard = relationship("Dashboard", back_populates="elements")


class KnowledgeBaseCategory(Base):
    """Категории документов в библиотеке знаний"""
    __tablename__ = "knowledge_base_categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)  # Название категории
    description = Column(Text, nullable=True)   # Описание категории
    icon = Column(String(50), default="📁")     # Иконка для отображения
    order_index = Column(Integer, default=0)    # Порядок сортировки
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    documents = relationship("KnowledgeBaseDocument", back_populates="category", cascade="all, delete-orphan")


class KnowledgeBaseDocument(Base):
    """Документы в библиотеке знаний"""
    __tablename__ = "knowledge_base_documents"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)  # Название документа
    description = Column(Text, nullable=True)    # Описание документа
    category_id = Column(Integer, ForeignKey("knowledge_base_categories.id", ondelete="SET NULL"), nullable=True)
    
    # Метаданные
    document_type = Column(String(50), default="document")  # document, instruction, order, method, presentation
    file_extension = Column(String(20), nullable=True)      # .pdf, .docx, .xlsx, .jpg, etc.
    file_size = Column(Integer, default=0)                   # Размер в байтах
    file_path = Column(String(500), nullable=False)         # Путь к файлу
    cover_image_path = Column(String(500), nullable=True)    # Путь к обложке/превью
    
    # Статистика
    downloads_count = Column(Integer, default=0)            # Счетчик скачиваний
    views_count = Column(Integer, default=0)                 # Счетчик просмотров
    
    # Для поиска
    tags = Column(String(500), nullable=True)                # Теги через запятую
    
    # Кто загрузил
    uploaded_by = Column(Integer, ForeignKey("users.id"), nullable=True)
    uploaded_by_name = Column(String(200), nullable=True)
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_published = Column(Boolean, default=True)
    is_featured = Column(Boolean, default=False)             # Рекомендуемый документ
    
    # Связи
    category = relationship("KnowledgeBaseCategory", back_populates="documents")
    uploader = relationship("User", foreign_keys=[uploaded_by])


class KnowledgeBaseFavorite(Base):
    """Избранные документы пользователей"""
    __tablename__ = "knowledge_base_favorites"
    
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    document_id = Column(Integer, ForeignKey("knowledge_base_documents.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Связи
    user = relationship("User", foreign_keys=[user_id])
    document = relationship("KnowledgeBaseDocument", foreign_keys=[document_id])


class KnowledgeBaseSearchLog(Base):
    """Лог поиска для аналитики"""
    __tablename__ = "knowledge_base_search_log"
    
    id = Column(Integer, primary_key=True, index=True)
    query = Column(String(500), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    results_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)