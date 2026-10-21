"""Import a stopped legacy SQLite snapshot into an EMPTY migrated PostgreSQL DB.

Keeps all identifiers and owner_id values, including NULL (never guesses ownership).
Refuses unknown nonempty data rather than silently dropping it. No personal values in logs.
"""
import argparse
from datetime import datetime, timezone
from pathlib import Path
import sqlite3

from sqlalchemy import Boolean, DateTime, MetaData, create_engine, text
from app.core.config import settings

ORDER=['users','courses','course_structure','modules','lessons','tasks','tests','course_modules',
       'theories','feedback','course_versions','module_versions','lesson_versions']
RENAMES={'lessons':{'lesson':'title'}}


def import_snapshot(path:Path, target_url:str):
    if not target_url.startswith('postgresql'):
        raise ValueError('Destination must be PostgreSQL')
    source=sqlite3.connect(f'file:{path.resolve()}?mode=ro',uri=True)
    source.row_factory=sqlite3.Row
    names=[row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    engine=create_engine(target_url,hide_parameters=True)
    metadata=MetaData();metadata.reflect(bind=engine)
    for name in names:
        if name not in ORDER and name not in ('sqlite_sequence','alembic_version'):
            if source.execute('SELECT count(*) FROM "'+name.replace('"','""')+'"').fetchone()[0]:
                raise ValueError('Unknown nonempty source table: '+name)
    counts={};now=datetime.now(timezone.utc)
    with engine.begin() as dest:
        for name,table in metadata.tables.items():
            if name!='alembic_version' and dest.execute(table.select().limit(1)).first():
                raise ValueError('Destination is not empty: '+name)
        for name in ORDER:
            if name not in names:continue
            rows=source.execute('SELECT * FROM "'+name+'"').fetchall()
            if not rows:counts[name]=0;continue
            if name not in metadata.tables:raise ValueError('Missing destination table: '+name)
            table=metadata.tables[name];prepared=[]
            for row in rows:
                values={RENAMES.get(name,{}).get(key,key):row[key] for key in row.keys()}
                unknown=set(values)-set(table.c.keys())
                if unknown:raise ValueError('Unmapped source columns: '+name+': '+','.join(sorted(unknown)))
                for column in table.columns:
                    if column.name not in values:
                        if name=='courses' and column.name=='status':values[column.name]='ready'
                        elif column.name=='revision' and name in ('modules','lessons','tasks','tests','theories'):values[column.name]=1
                        elif name=='modules' and column.name=='description':values[column.name]=''
                        elif column.name=='position' and name in ('modules','lessons','tasks','tests'):
                            group_key='course_id' if name=='modules' else 'module_id'
                            values[column.name]=sum(1 for previous in prepared if previous.get(group_key)==values.get(group_key))
                        elif column.name in ('created_at','updated_at'):values[column.name]=now
                        elif column.name=='is_deleted':values[column.name]=False
                        elif not column.nullable and column.server_default is None and not column.primary_key:
                            raise ValueError('Required destination column has no default: '+name+'.'+column.name)
                        continue
                    value=values[column.name]
                    if value is not None and isinstance(column.type,DateTime) and isinstance(value,str):
                        values[column.name]=datetime.fromisoformat(value)
                    if value is not None and isinstance(column.type,Boolean):
                        if value not in (0,1,False,True):raise ValueError('Invalid boolean: '+name+'.'+column.name)
                        values[column.name]=bool(value)
                prepared.append(values)
            dest.execute(table.insert(),prepared)
            counts[name]=len(prepared)
            if 'id' in table.c:
                dest.execute(text("SELECT setval(pg_get_serial_sequence(:table_name, 'id'), (SELECT MAX(id) FROM \""+name+"\"), true)"),{'table_name':name})
        for name,expected in counts.items():
            actual=dest.execute(text('SELECT count(*) FROM "'+name+'"')).scalar_one()
            if actual!=expected:raise ValueError('Row count mismatch: '+name)
    source.close();engine.dispose()
    return counts


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('snapshot',type=Path)
    parser.add_argument('--confirm-empty-destination',action='store_true')
    args=parser.parse_args()
    if not args.confirm_empty_destination:parser.error('Explicit --confirm-empty-destination is required')
    print(import_snapshot(args.snapshot,settings.DATABASE_URL))

if __name__=='__main__':main()
