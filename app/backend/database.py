from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker



                            #postgresql:// — протокол подключения к PostgreSQL

                            #app_user:password — логин и пароль пользователя БД

                            #localhost:5432 — адрес сервера и порт

                            #auth_db — название базы данных
# Подключение к auth_db
AUTH_DATABASE_URL = "postgresql://app_user:password@localhost:5432/auth_db"
auth_engine = create_engine(AUTH_DATABASE_URL) ## Создание движка для content_db
AuthSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=auth_engine)

# Подключение к content_db
CONTENT_DATABASE_URL = "postgresql://app_user:password@localhost:5432/content_db"
content_engine = create_engine(CONTENT_DATABASE_URL)
ContentSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=content_engine)
# Фабрика сессий для auth_db
                            #sessionmaker — генератор сессий для работы с БД.

                            #Каждая сессия — это отдельное подключение к БД для выполнения запросов.
                            
                            
Base = declarative_base()
                            #declarative_base() — базовый класс для всех моделей.

                            #Позволяет описывать таблицы как Python-классы (см. auth_models.py и content_models.py).

# Генераторы сессий
def get_auth_db():
    db = AuthSessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_content_db():
    db = ContentSessionLocal()
    try:
        yield db
    finally:
        db.close()