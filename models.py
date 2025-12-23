from sqlalchemy import Column, Integer, String
from database import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    role = Column(String, default="user") # user, municipal_admin, regional_admin
    unit_name = Column(String)
    district = Column(String)
    food_type = Column(String)
    url_1c = Column(String, default="https://cemon.ru/MSHP/ru/") # Ссылка на 1С