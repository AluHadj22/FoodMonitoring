from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey
from sqlalchemy.orm import relationship

# Отдельная база данных для библиотеки знаний
KNOWLEDGE_BASE_DATABASE_URL = "sqlite:///./knowledge_base.db"

engine = create_engine(
    KNOWLEDGE_BASE_DATABASE_URL,
    connect_args={"check_same_thread": False}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Модели данных для библиотеки знаний (прямо здесь, чтобы не трогать основную models.py)

class KnowledgeBaseCategory(Base):
    """Категории документов в библиотеке знаний"""
    __tablename__ = "kb_categories"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)
    icon = Column(String(50), default="📁")
    color = Column(String(20), default="#667eea")  # Цвет категории
    order_index = Column(Integer, default=0)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    documents = relationship("KnowledgeBaseDocument", back_populates="category", cascade="all, delete-orphan")


class KnowledgeBaseDocument(Base):
    """Документы в библиотеке знаний"""
    __tablename__ = "kb_documents"
    
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    category_id = Column(Integer, ForeignKey("kb_categories.id", ondelete="SET NULL"), nullable=True)
    
    document_type = Column(String(50), default="document")  # document, instruction, order, method, presentation
    file_extension = Column(String(20), nullable=True)
    file_size = Column(Integer, default=0)
    file_path = Column(String(500), nullable=False)
    cover_image_path = Column(String(500), nullable=True)
    
    downloads_count = Column(Integer, default=0)
    views_count = Column(Integer, default=0)
    likes_count = Column(Integer, default=0)
    
    tags = Column(String(500), nullable=True)
    
    uploaded_by = Column(String(200), nullable=True)  # Имя загрузившего
    uploaded_by_email = Column(String(200), nullable=True)  # Email для связи
    
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    is_published = Column(Boolean, default=True)
    is_featured = Column(Boolean, default=False)
    
    category = relationship("KnowledgeBaseCategory", back_populates="documents")
    favorites = relationship("KnowledgeBaseFavorite", back_populates="document", cascade="all, delete-orphan")
    comments = relationship("KnowledgeBaseComment", back_populates="document", cascade="all, delete-orphan")


class KnowledgeBaseFavorite(Base):
    """Избранные документы"""
    __tablename__ = "kb_favorites"
    
    id = Column(Integer, primary_key=True, index=True)
    user_email = Column(String(200), nullable=False, index=True)  # Email пользователя
    document_id = Column(Integer, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    document = relationship("KnowledgeBaseDocument", back_populates="favorites")


class KnowledgeBaseComment(Base):
    """Комментарии к документам"""
    __tablename__ = "kb_comments"
    
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("kb_documents.id", ondelete="CASCADE"), nullable=False)
    user_name = Column(String(200), nullable=True)  # Имя пользователя
    user_email = Column(String(200), nullable=True)  # Email
    content = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    is_approved = Column(Boolean, default=False)  # Для модерации
    
    document = relationship("KnowledgeBaseDocument", back_populates="comments")


class KnowledgeBaseSearchLog(Base):
    """Лог поиска для аналитики"""
    __tablename__ = "kb_search_log"
    
    id = Column(Integer, primary_key=True, index=True)
    query = Column(String(500), nullable=False)
    user_email = Column(String(200), nullable=True)
    results_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class KnowledgeBaseAdmin(Base):
    """Администраторы библиотеки"""
    __tablename__ = "kb_admins"
    
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(200), unique=True, nullable=False)
    name = Column(String(200), nullable=True)
    access_code = Column(String(100), nullable=False)  # Хешированный код доступа
    last_login = Column(DateTime, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# Создаём таблицы
def init_db():
    Base.metadata.create_all(bind=engine)

def get_kb_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()