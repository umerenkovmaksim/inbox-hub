from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from university_pz_parser.settings import get_settings

settings = get_settings()
engine = create_engine(settings.database.url)

Session = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass
