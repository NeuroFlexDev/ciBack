from logging.config import fileConfig
from sqlalchemy import create_engine
from sqlalchemy import engine_from_config
from sqlalchemy import pool
from app.database.db import Base
from app.models.course import Course  # Убедись, что импортировал модель
from app.models.lesson import Lesson  # Убедись, что импортировал модель
from app.models.module import Module  # Убедись, что импортировал модель
from app.models.task import Task  # Убедись, что импортировал модель
from app.models.test import Test  # Убедись, что импортировал модель
from app.models.generate_request import GenerateRequest  # Убедись, что импортировал модель
from app.models.course_structure import CourseStructure  # Убедись, что импортировал модель
from app.models.feedback import Feedback  # Убедись, что импортировал модель
from app.models.course_version import CourseVersion  # Убедись, что импортировал модель
from app.models.lesson_version import LessonVersion  # Убедись, что импортировал модель
from app.models.module_version import ModuleVersion  # Убедись, что импортировал модель


from alembic import context

# this is the Alembic Config object, which provides
# access to the values within the .ini file in use.
config = context.config

# Interpret the config file for Python logging.
# This line sets up loggers basically.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# add your model's MetaData object here
# for 'autogenerate' support
# from myapp import mymodel
# target_metadata = mymodel.Base.metadata
target_metadata = Base.metadata

# other values from the config, defined by the needs of env.py,
# can be acquired:
# my_important_option = config.get_main_option("my_important_option")
# ... etc.


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode.

    This configures the context with just a URL
    and not an Engine, though an Engine is acceptable
    here as well.  By skipping the Engine creation
    we don't even need a DBAPI to be available.

    Calls to context.execute() here emit the given string to the
    script output.

    """
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode.

    In this scenario we need to create an Engine
    and associate a connection with the context.

    """
    DATABASE_URL = "postgresql://nosignalx2k:Accessors231@localhost:5432/neurolearn"
    connectable = create_engine(DATABASE_URL, echo=True)


    with connectable.connect() as connection:
        context.configure(
            connection=connection, target_metadata=target_metadata
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
