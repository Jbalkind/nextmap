# Dual Database Backend Support

This document describes the new dual database backend support that allows EMAP to work with both SQLite and PostgreSQL databases.

## Overview

The EMAP tool now supports both SQLite and PostgreSQL database backends through a unified interface. This allows users to:

- Use SQLite for development and testing (lightweight, no setup required)
- Use PostgreSQL for production deployments (better performance, concurrent access)
- Switch between backends easily without changing code
- Maintain backward compatibility with existing code

## Quick Start

### Using SQLite (Default)

```python
from emap import NetlistDB

# In-memory database (default)
db = NetlistDB('schema.sql')

# File-based database
db = NetlistDB('schema.sql', 'my_netlist.db')
```

### Using PostgreSQL

```python
from emap import NetlistDB

# Local PostgreSQL with default settings
db = NetlistDB('schema.sql', {'database': 'my_netlist'}, backend='postgres')

# Remote PostgreSQL
db_config = {
    'host': 'localhost',
    'port': 5432,
    'database': 'my_netlist',
    'user': 'myuser',
    'password': 'mypassword'
}
db = NetlistDB('schema.sql', db_config, backend='postgres')
```

### Using Environment Configuration

```python
# Set backend through configuration
from database_config import use_postgres_local
use_postgres_local('my_netlist')

# Now NetlistDB will use PostgreSQL automatically
from emap import NetlistDB
db = NetlistDB('schema.sql')  # Uses PostgreSQL
```

### Using the New Unified Interface

```python
from emap import create_netlist_db

# SQLite
db = create_netlist_db('schema.sql', backend='sqlite', db_file='my_netlist.db')

# PostgreSQL
db = create_netlist_db('schema.sql', backend='postgres',
                      database='my_netlist', host='localhost')
```

## Configuration Options

### Environment Variables

You can configure the backend using environment variables:

```bash
# Use SQLite (default)
export EMAP_DB_BACKEND=sqlite
export EMAP_SQLITE_FILE=my_database.db

# Use PostgreSQL
export EMAP_DB_BACKEND=postgres
export EMAP_POSTGRES_DATABASE=my_netlist
export EMAP_POSTGRES_HOST=localhost
export EMAP_POSTGRES_PORT=5432
export EMAP_POSTGRES_USER=myuser
export EMAP_POSTGRES_PASSWORD=mypassword
```

### Configuration Helper

```python
from database_config import DatabaseConfig

# Check current configuration
DatabaseConfig.print_current_config()

# Set SQLite backend
DatabaseConfig.set_sqlite_backend('/path/to/database.db')

# Set PostgreSQL backend
DatabaseConfig.set_postgres_backend(
    database='my_netlist',
    host='localhost',
    port=5432,
    user='myuser',
    password='mypassword'
)
```

## Backward Compatibility

The existing code will continue to work without changes:

```python
# This still works exactly as before
from emap import NetlistDB
db = NetlistDB('schema.sql', 'database.db')
```

The system automatically detects the backend based on the parameters:
- String parameter → SQLite backend
- Dict parameter → PostgreSQL backend
- Can be overridden with explicit `backend='sqlite'` or `backend='postgres'`

## Implementation Details

### Architecture

The dual backend support is implemented through:

1. **Database Adapters**: Separate adapter classes for SQLite and PostgreSQL
2. **Unified Interface**: A common `NetlistDB` class that delegates to adapters
3. **Backward Compatibility**: Existing interfaces are preserved
4. **Configuration System**: Environment-based and programmatic configuration

### Key Files

- `emap/db_interface.py`: Unified NetlistDB interface
- `emap/db_sqlite.py`: SQLite adapter and backward-compatible class
- `emap/db_postgres.py`: PostgreSQL adapter and backward-compatible class
- `emap/__init__.py`: Updated to provide factory functions
- `database_config.py`: Configuration utilities
- `test_dual_backend.py`: Test suite for both backends

### Database Differences Handled

The implementation handles key differences between SQLite and PostgreSQL:

1. **Parameter Placeholders**: `?` (SQLite) vs `%s` (PostgreSQL)
2. **Insert Ignore Syntax**: `INSERT OR IGNORE` (SQLite) vs `ON CONFLICT DO NOTHING` (PostgreSQL)
3. **Connection Handling**: File-based (SQLite) vs network-based (PostgreSQL)
4. **System Tables**: `sqlite_master` vs `pg_tables`

## Migration Guide

### From SQLite-only to Dual Backend

If you have existing code using SQLite, you can migrate gradually:

1. **No changes needed**: Existing code continues to work
2. **Explicit backend**: Add `backend='sqlite'` for clarity
3. **Switch to PostgreSQL**: Change the second parameter to a dict and add `backend='postgres'`

### Setting Up PostgreSQL

1. Install PostgreSQL server
2. Create a database: `createdb my_netlist`
3. Install Python PostgreSQL driver: `pip install psycopg2-binary`
4. Configure connection parameters

Example setup:
```sql
-- As PostgreSQL superuser
CREATE DATABASE my_netlist;
CREATE USER myuser WITH PASSWORD 'mypassword';
GRANT ALL PRIVILEGES ON DATABASE my_netlist TO myuser;
```

## Testing

Run the test suite to verify both backends work:

```bash
python3 test_dual_backend.py
```

This will test:
- SQLite backend functionality
- PostgreSQL backend functionality (if available)
- Unified interface
- Configuration system

## Performance Considerations

### SQLite
- **Pros**: No setup, fast for small datasets, embedded
- **Cons**: No concurrent writes, limited scalability
- **Best for**: Development, testing, single-user scenarios

### PostgreSQL
- **Pros**: Excellent performance, concurrent access, scalability
- **Cons**: Requires setup, more complex deployment
- **Best for**: Production, multi-user, large datasets

## Future Enhancements

Planned improvements include:

1. **Schema Migration**: Automatic conversion between SQLite and PostgreSQL schemas
2. **Connection Pooling**: Connection pool support for PostgreSQL
3. **Additional Backends**: Support for other databases (MySQL, etc.)
4. **Performance Optimization**: Backend-specific optimizations
5. **Monitoring**: Database performance monitoring and logging

## Troubleshooting

### Common Issues

1. **PostgreSQL not available**: The test will skip PostgreSQL tests if the server isn't available
2. **Permission denied**: Ensure the PostgreSQL user has proper permissions
3. **Module not found**: Install required dependencies (`psycopg2-binary` for PostgreSQL)

### Debug Information

```python
from database_config import DatabaseConfig
DatabaseConfig.print_current_config()
```

This will show the current backend configuration and help identify issues.

## Examples

See `test_dual_backend.py` for comprehensive examples of using both backends.

The dual backend support maintains full compatibility while providing the flexibility to choose the best database for your use case.