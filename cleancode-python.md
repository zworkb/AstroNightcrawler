# Clean Code - Python Ergänzung

> Python-spezifische Regeln als Ergänzung zum [Clean Code Manifesto](cleancode.md), welches auch eingelesen werden soll



Dieses Dokument enthält Python-spezifische Coding-Standards und Best Practices.

---

## Python Style Guide

### Grundlagen

| Regel | Standard |
|-------|----------|
| **Style Guide** | PEP 8 |
| **Einrückung** | 4 Spaces (keine Tabs) |
| **Zeilenlänge** | Max. 100 Zeichen |
| **Encoding** | UTF-8 |
| **Python Version** | 3.11+ |

---

## Type Hints (Pflicht)

### Regeln

| Regel | Beschreibung |
|-------|--------------|
| **Alle Funktionen typisiert** | Parameter und Rückgabewerte |
| **Keine `Any` ohne Begründung** | `Any` nur wenn unvermeidbar |
| **Moderne Syntax** | `list[str]` statt `List[str]` (Python 3.9+) |
| **Optional explizit** | `str | None` statt implizites Optional |
| **Rückgabe `None`** | Explizit `-> None` angeben |

### Beispiele

```python
# ❌ Schlecht - keine Type Hints
def create_user(username, email, roles):
    ...

# ❌ Schlecht - veraltete Syntax
from typing import List, Optional
def get_users(active: Optional[bool] = None) -> List[User]:
    ...

# ✅ Gut - vollständig typisiert, moderne Syntax
def create_user(
    username: str,
    email: str,
    roles: list[str] | None = None,
) -> User:
    ...

# ✅ Gut - None explizit
def delete_user(user_id: int) -> None:
    ...
```

### Komplexe Typen

```python
from collections.abc import Callable, Iterator
from typing import TypeVar

T = TypeVar("T")

# Callable mit Signatur
def apply_filter(
    items: list[T],
    predicate: Callable[[T], bool],
) -> list[T]:
    ...

# Generator
def iterate_pages(url: str) -> Iterator[Page]:
    ...
```

---

## Docstrings (Google-Style)

### Regeln

| Regel | Beschreibung |
|-------|--------------|
| **Alle öffentlichen Funktionen** | Docstring erforderlich |
| **Google-Style Format** | Args, Returns, Raises Sektionen |
| **Business Rules dokumentiert** | BR-Codes referenzieren |
| **Keine privaten Methoden** | `_private_method` braucht keinen Docstring |

### Format

```python
def create_invoice(
    patient_id: int,
    appointment_ids: list[int],
    db: Session,
) -> Invoice:
    """Create a new invoice for the given appointments.

    Implements Business Rules:
    - BR-INV-01: Invoice number must be unique
    - BR-INV-02: Only completed appointments can be billed

    Args:
        patient_id: The ID of the patient to bill.
        appointment_ids: List of appointment IDs to include.
        db: Database session.

    Returns:
        The created Invoice object.

    Raises:
        ValueError: If no valid appointments provided.
        PatientNotFoundError: If patient doesn't exist.

    Example:
        >>> invoice = create_invoice(patient_id=1, appointment_ids=[1, 2], db=session)
        >>> print(invoice.invoice_number)
        'R2025-0001'
    """
    ...
```

### Klassen-Docstrings

```python
class InvoiceService:
    """Service for invoice operations.

    Handles creation, modification, and querying of invoices.
    All methods are stateless and require an explicit database session.

    Implements Business Rules:
    - BR-INV-01: Invoice number format R{YEAR}-{NUMBER}
    - BR-INV-02: Only completed appointments can be billed
    - BR-INV-03: Appointments can only be billed once

    Attributes:
        None (stateless service)

    Example:
        >>> service = InvoiceService()
        >>> invoice = service.create_invoice(patient_id=1, appointments=[1,2], db=session)
    """
```

---

## Import-Sortierung

Imports werden in dieser Reihenfolge sortiert (durch `ruff` erzwungen):

1. **Standard Library** (`os`, `sys`, `datetime`)
2. **Third-Party** (`fastapi`, `kivy`, `pydantic`)
3. **Local** (`from myproject.models import ...`)

```python
# ✅ Korrekte Reihenfolge
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends
from kivy.app import App
from pydantic import BaseModel

from myproject.config import settings
from myproject.models import User
from myproject.services import UserService
```

### Regeln

| Regel | Beschreibung |
|-------|--------------|
| **Alphabetisch** | Innerhalb jeder Gruppe |
| **Absolute Imports** | `from myproject.module...` statt relative |
| **Keine Wildcard** | Kein `from module import *` |
| **Keine ungenutzten** | Alle Imports müssen verwendet werden |

---

## Namenskonventionen

### Python-spezifisch

| Element | Konvention | Beispiel |
|---------|------------|----------|
| **Module** | snake_case | `user_service.py` |
| **Klassen** | PascalCase | `UserService` |
| **Funktionen** | snake_case | `create_user()` |
| **Variablen** | snake_case | `user_name` |
| **Konstanten** | UPPER_SNAKE_CASE | `MAX_RETRIES` |
| **Private** | _prefix | `_validate_email()` |
| **"Dunder"** | __double__ | `__init__()` |

### Beispiele

```python
# ✅ Korrekt
MAX_RETRY_ATTEMPTS = 3

class UserService:
    def create_user(self, user_name: str) -> User:
        self._validate_input(user_name)
        ...

    def _validate_input(self, value: str) -> None:
        ...

# ❌ Falsch
maxRetryAttempts = 3  # camelCase

class userService:  # lowercase
    def CreateUser(self):  # PascalCase für Methode
        ...
```

---

## Fehlerbehandlung

### Custom Exceptions

```python
# ✅ Gut - Domain-spezifische Exceptions
class PatientNotFoundError(Exception):
    """Raised when a patient is not found."""

    def __init__(self, patient_id: int):
        self.patient_id = patient_id
        super().__init__(f"Patient with ID {patient_id} not found")


class InvoiceValidationError(ValueError):
    """Raised when invoice validation fails."""

    def __init__(self, message: str, field: str | None = None):
        self.field = field
        super().__init__(message)
```

### Exception Handling

```python
# ❌ Schlecht - zu breit
try:
    process_payment()
except Exception:
    pass

# ❌ Schlecht - String-Matching
try:
    db.commit()
except Exception as e:
    if "UNIQUE constraint" in str(e):
        ...

# ✅ Gut - spezifische Exceptions
from sqlalchemy.exc import IntegrityError

try:
    db.commit()
except IntegrityError:
    db.rollback()
    raise DuplicateEntryError("Invoice number already exists")
```

### Context Manager für Cleanup

```python
# ✅ Gut - automatisches Cleanup
from contextlib import contextmanager

@contextmanager
def temporary_file(suffix: str = ".tmp"):
    """Create a temporary file that is deleted after use."""
    path = Path(tempfile.mktemp(suffix=suffix))
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)
```

---

## Dataclasses und Pydantic

### Wann was verwenden

| Use Case | Empfehlung |
|----------|------------|
| **Einfache Datencontainer** | `@dataclass` |
| **API Request/Response** | Pydantic `BaseModel` |
| **Konfiguration** | Pydantic `BaseSettings` |
| **DB Models** | SQLModel |
| **UI State** | `@dataclass` |

### Beispiele

```python
from dataclasses import dataclass, field
from pydantic import BaseModel, Field

# ✅ Dataclass für internen State
@dataclass
class PaginationState:
    """Pagination state for UI."""
    current_page: int = 0
    page_size: int = 10
    total_items: int = 0

    @property
    def total_pages(self) -> int:
        return (self.total_items + self.page_size - 1) // self.page_size


# ✅ Pydantic für API
class UserCreate(BaseModel):
    """Schema for user creation request."""
    username: str = Field(..., min_length=3, max_length=50)
    email: str = Field(..., pattern=r"^[\w\.-]+@[\w\.-]+\.\w+$")
    password: str = Field(..., min_length=8)


# ✅ SQLModel für DB
class User(SQLModel, table=True):
    """User database model."""
    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True)
    email: str = Field(unique=True)
```

---

## Tooling

### Projekt-Tools

| Tool | Zweck | Befehl |
|------|-------|--------|
| **ruff** | Linting & Formatting | `ruff check .` / `ruff format .` |
| **mypy** | Type Checking | `mypy src/` |
| **pytest** | Testing | `pytest` |
| **pytest-cov** | Coverage | `pytest --cov=src` |

### Vor jedem Commit

```bash
# Alle Checks auf einmal
ruff check . && ruff format --check . && mypy src/ && pytest

# Oder mit pre-commit hooks (empfohlen)
pre-commit run --all-files
```

### pyproject.toml Konfiguration

```toml
[tool.ruff]
line-length = 100
target-version = "py311"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.mypy]
python_version = "3.11"
strict = true
warn_return_any = true
```

---

## 3rd-Party Framework Patterns

### FastAPI

#### Grundregeln

| Regel | Beschreibung |
|-------|--------------|
| **Dependency Injection** | `Depends()` für DB-Sessions, Auth, Services |
| **Response Models** | Immer `response_model=` für API-Dokumentation |
| **Status Codes explizit** | `status_code=status.HTTP_201_CREATED` |
| **Exception Mapping** | Domain-Exceptions → HTTPException |
| **Async nur wenn nötig** | `def` für sync DB-Operationen, `async def` nur für I/O |

#### Router-Struktur

```python
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session

router = APIRouter(prefix="/users", tags=["users"])


@router.post("/", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def create_user(
    user_data: UserCreate,
    db: Session = Depends(get_session),
) -> User:
    """Create a new user.

    Args:
        user_data: User creation data.
        db: Database session.

    Returns:
        Created user.

    Raises:
        HTTPException: 409 if user already exists.
    """
    try:
        return UserService.create_user(user_data, db)
    except UserAlreadyExistsError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(e),
        )


@router.get("/{user_id}", response_model=UserResponse)
def get_user(
    user_id: int,
    db: Session = Depends(get_session),
) -> User:
    """Get user by ID."""
    user = db.get(User, user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"User {user_id} not found",
        )
    return user
```

#### Dependency Injection Pattern

```python
from collections.abc import Generator
from fastapi import Depends

def get_session() -> Generator[Session, None, None]:
    """Database session dependency."""
    with Session(engine) as session:
        yield session


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_session),
) -> User:
    """Get authenticated user from token."""
    user = AuthService.verify_token(token, db)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    return user


# Verwendung: Dependency Chain
@router.get("/me", response_model=UserResponse)
def get_current_user_profile(
    current_user: User = Depends(get_current_user),
) -> User:
    return current_user
```

---

### NiceGUI

#### Grundregeln

| Regel | Beschreibung |
|-------|--------------|
| **State in Dataclasses** | UI-State in `@dataclass`, nicht lose Variablen |
| **Komponenten-Klassen** | Komplexe UI als Klasse mit `render()` Methode |
| **Private Render-Methoden** | `_render_*()` für Teilbereiche |
| **Context Manager** | `with ui.column():` für Layout-Hierarchie |
| **Quasar-Klassen** | `.classes("w-full gap-4")` für Styling |

#### Page-Struktur

```python
from dataclasses import dataclass
from nicegui import ui


@dataclass
class FilterState:
    """State for list filters."""
    search_query: str = ""
    status: str | None = None
    page: int = 0
    page_size: int = 20


class UserListPage:
    """User list page with search, filters, and pagination."""

    def __init__(self) -> None:
        self.filter_state = FilterState()
        self._users: list[User] = []

    def render(self) -> None:
        """Render the complete page."""
        with ui.column().classes("w-full gap-4 p-4"):
            self._render_header()
            self._render_filters()
            self._render_table()
            self._render_pagination()

    def _render_header(self) -> None:
        """Render page header with title and actions."""
        with ui.row().classes("w-full justify-between items-center"):
            ui.label("Benutzer").classes("text-h5")
            ui.button("Neuer Benutzer", icon="add", on_click=self._open_create_dialog)

    def _render_filters(self) -> None:
        """Render filter controls."""
        with ui.row().classes("w-full gap-4"):
            ui.input(
                "Suche",
                on_change=lambda e: self._on_search(e.value),
            ).classes("flex-grow")
            ui.select(
                ["Alle", "Aktiv", "Inaktiv"],
                value="Alle",
                on_change=lambda e: self._on_status_filter(e.value),
            )

    def _render_table(self) -> None:
        """Render user table."""
        columns = [
            {"name": "username", "label": "Benutzername", "field": "username"},
            {"name": "email", "label": "E-Mail", "field": "email"},
            {"name": "status", "label": "Status", "field": "status"},
        ]
        ui.table(columns=columns, rows=self._get_rows()).classes("w-full")

    def _on_search(self, query: str) -> None:
        """Handle search input change."""
        self.filter_state.search_query = query
        self._reload_data()

    def _reload_data(self) -> None:
        """Reload data from backend."""
        # Implementation...
```

#### Reaktive Updates

```python
from nicegui import ui

# ❌ Schlecht - Manuelles Update vergessen
class BadExample:
    def __init__(self):
        self.count = 0
        self.label = ui.label(f"Count: {self.count}")

    def increment(self):
        self.count += 1
        # Label zeigt immer noch alten Wert!


# ✅ Gut - ui.refreshable für automatische Updates
class GoodExample:
    def __init__(self):
        self.count = 0
        self._render_count()

    @ui.refreshable
    def _render_count(self) -> None:
        ui.label(f"Count: {self.count}")

    def increment(self) -> None:
        self.count += 1
        self._render_count.refresh()  # Triggert Neu-Rendering
```

#### Dialog-Pattern

```python
def show_confirm_dialog(
    title: str,
    message: str,
    on_confirm: Callable[[], None],
) -> None:
    """Show confirmation dialog."""
    with ui.dialog() as dialog, ui.card():
        ui.label(title).classes("text-h6")
        ui.label(message)
        with ui.row().classes("w-full justify-end gap-2"):
            ui.button("Abbrechen", on_click=dialog.close)
            ui.button(
                "Bestätigen",
                on_click=lambda: (on_confirm(), dialog.close()),
            ).props("color=primary")
    dialog.open()
```

---

### SQLModel / SQLAlchemy

#### Grundregeln

| Regel | Beschreibung |
|-------|--------------|
| **Session als Context Manager** | `with Session(engine) as db:` |
| **Eager Loading** | `selectinload()` / `joinedload()` für Relations |
| **Kein N+1** | Relations in Query laden, nicht in Loop |
| **Ein Commit pro Operation** | Nicht mehrfach committen |
| **Rollback bei Fehler** | `try/except` mit `db.rollback()` |

#### Model-Definition

```python
from datetime import datetime, timezone
from decimal import Decimal

from sqlmodel import Field, Relationship, SQLModel


class User(SQLModel, table=True):
    """User database model."""

    __tablename__ = "users"

    id: int | None = Field(default=None, primary_key=True)
    username: str = Field(unique=True, index=True, max_length=50)
    email: str = Field(unique=True, index=True)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    # Relationships
    orders: list["Order"] = Relationship(back_populates="user")


class Order(SQLModel, table=True):
    """Order database model."""

    __tablename__ = "orders"

    id: int | None = Field(default=None, primary_key=True)
    user_id: int = Field(foreign_key="users.id", index=True)
    total: Decimal = Field(max_digits=10, decimal_places=2)

    # Relationships
    user: User | None = Relationship(back_populates="orders")
    items: list["OrderItem"] = Relationship(back_populates="order")
```

#### Query Patterns

```python
from sqlmodel import Session, select
from sqlalchemy.orm import selectinload


# ❌ Schlecht - N+1 Query Problem
def get_users_with_orders_bad(db: Session) -> list[User]:
    users = db.exec(select(User)).all()
    for user in users:
        _ = user.orders  # Lazy Load = 1 Query pro User!
    return users


# ✅ Gut - Eager Loading
def get_users_with_orders(db: Session) -> list[User]:
    statement = select(User).options(selectinload(User.orders))
    return list(db.exec(statement).all())


# ✅ Gut - Filtered Query
def get_active_users(db: Session, search: str | None = None) -> list[User]:
    statement = select(User).where(User.is_active == True)

    if search:
        statement = statement.where(
            User.username.ilike(f"%{search}%")  # type: ignore[attr-defined]
        )

    statement = statement.order_by(User.username)
    return list(db.exec(statement).all())


# ✅ Gut - Session als Context Manager mit Fehlerbehandlung
def create_order(user_id: int, items: list[dict]) -> Order:
    with Session(engine) as db:
        try:
            order = Order(user_id=user_id, total=Decimal("0.00"))
            db.add(order)

            for item_data in items:
                item = OrderItem(order=order, **item_data)
                db.add(item)
                order.total += item.price * item.quantity

            db.commit()
            db.refresh(order)
            return order
        except Exception:
            db.rollback()
            raise
```

#### Transaktionen

```python
# ❌ Schlecht - Mehrere Commits
def bad_transaction(db: Session) -> None:
    user = User(username="test")
    db.add(user)
    db.commit()  # Commit 1

    order = Order(user_id=user.id)
    db.add(order)
    db.commit()  # Commit 2 - Inkonsistenz möglich!


# ✅ Gut - Ein Commit am Ende
def good_transaction(db: Session) -> None:
    user = User(username="test")
    db.add(user)
    db.flush()  # ID generieren ohne Commit

    order = Order(user_id=user.id)
    db.add(order)

    db.commit()  # Alles oder nichts
```

### Kivy

#### Event-Handling

| Regel | Beschreibung |
|-------|--------------|
| **Events registrieren** | `register_event_type()` VOR `super().__init__()` |
| **Unbind nach Entfernung** | Widgets MÜSSEN nach `remove_widget()` / `dismiss()` unbound werden |
| **Keine Callback-Leaks** | Callbacks auf `None` setzen vor Cleanup |

```python
# ❌ Schlecht - Event-Leak
def remove_overlay(self):
    self.overlay.dismiss()
    self.overlay = None

# ✅ Gut - Proper Unbinding
def remove_overlay(self):
    self.overlay.unbind(on_close=self._on_overlay_close)
    self.overlay.dismiss()
    self.overlay = None


# ✅ Gut - Events vor super().__init__() registrieren
class MyWidget(EventDispatcher):
    def __init__(self, **kwargs):
        self.register_event_type("on_custom_event")
        super().__init__(**kwargs)
```

#### Kivy Properties vs Python Attributes

| Regel | Beschreibung |
|-------|--------------|
| **Properties für UI-Bindings** | `StringProperty`, `ObjectProperty` wenn KV-Bindung nötig |
| **Normale Attribute für Internes** | `self._internal` für nicht-reaktive Daten |
| **Keine Class-Level mutable Defaults** | `_event = None` auf Class-Level → in `__init__` |

```python
# ❌ Schlecht - Class-Level None kann zwischen Instanzen geteilt werden
class MyController(EventDispatcher):
    _poll_event = None  # Problematisch!
    _callbacks = []     # BUG: Liste wird geteilt!

# ✅ Gut - In __init__ initialisieren
class MyController(EventDispatcher):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._poll_event = None
        self._callbacks = []
```

#### KV-Language

| Regel | Beschreibung |
|-------|--------------|
| **Inline KV max. 50 Zeilen** | Längere Definitionen in `.kv`-Dateien auslagern |
| **Keine Logik in KV** | Nur Bindings, keine komplexen Ausdrücke |
| **IDs dokumentieren** | Wichtige `id:`-Referenzen im Docstring der Klasse erwähnen |

```python
# ✅ Gut - Kurze inline KV-Definition
Builder.load_string("""
<StatusIndicator>:
    size_hint: (None, None)
    size: ('120dp', '24dp')
    Label:
        text: root.status_text
""")

# ❌ Schlecht - Zu lange inline KV (>50 Zeilen)
# → In separate .kv-Datei auslagern
```

#### Threading mit Kivy

| Regel | Beschreibung |
|-------|--------------|
| **UI-Updates nur im Main Thread** | `Clock.schedule_once()` für Thread→UI |
| **Daemon Threads für Background** | `daemon=True` bei kurzlebigen Tasks |

```python
import threading
from kivy.clock import Clock

# ✅ Korrekt - Thread-safe UI Update
def start_background_scan(self):
    def scan_in_background():
        result = expensive_operation()
        # UI-Update muss im Main Thread erfolgen!
        Clock.schedule_once(lambda dt: self._update_ui(result), 0)

    thread = threading.Thread(target=scan_in_background, daemon=True)
    thread.start()
```

#### Callback-Signaturen

| Regel | Beschreibung |
|-------|--------------|
| **Callback-Types explizit** | `Callable[[NFCTagData], None]` statt `Callable` |
| **Nicht `callable` verwenden** | `callable` ist builtin-Funktion, nicht Type Hint |

```python
from collections.abc import Callable

# ❌ Schlecht - callable ist keine Type Annotation
def fade_out(self, on_complete: callable = None) -> None:
    ...

# ✅ Gut - Explizite Callable-Signatur
def fade_out(self, on_complete: Callable[[], None] | None = None) -> None:
    ...

# ✅ Gut - Callback-Attribute typisieren
class DataReader:
    on_data_received: Callable[[DataPacket], None] | None = None
    on_connection_lost: Callable[[], None] | None = None
```

#### Testbarkeit mit Kivy

| Regel | Beschreibung |
|-------|--------------|
| **Dependency Injection** | Repository/Scanner als Parameter für Tests |
| **Keine Window-Abhängigkeit in Logik** | UI-Klassen von Business-Logik trennen |
| **Clock.tick() in Tests** | Für zeitabhängige Tests `Clock.tick()` verwenden |

```python
# ✅ Gut - Testbar durch Dependency Injection
class SettingsScreen(ModalView):
    def __init__(
        self,
        repository: Repository | None = None,  # DI für Tests
        service: SomeService | None = None,
        **kwargs,
    ):
        self._repository = repository or Repository()
        self._service = service or SomeService()
```

---

## Anti-Patterns

### Vermeiden

```python
# ❌ Mutable Default Arguments
def add_item(items: list = []):  # BUG: Liste wird geteilt!
    items.append("new")
    return items

# ✅ Korrekt
def add_item(items: list | None = None):
    if items is None:
        items = []
    items.append("new")
    return items


# ❌ Bare except
try:
    risky_operation()
except:  # Fängt auch SystemExit, KeyboardInterrupt!
    pass

# ✅ Korrekt
try:
    risky_operation()
except Exception:
    logger.exception("Operation failed")


# ❌ Global State
_cache = {}  # Modul-Level mutable state

def get_user(id: int) -> User:
    if id not in _cache:
        _cache[id] = load_user(id)
    return _cache[id]

# ✅ Korrekt - expliziter Cache
class UserCache:
    def __init__(self):
        self._cache: dict[int, User] = {}

    def get(self, id: int) -> User:
        ...
```

---

## Quellen

- [PEP 8 - Style Guide for Python Code](https://peps.python.org/pep-0008/)
- [PEP 484 - Type Hints](https://peps.python.org/pep-0484/)
- [PEP 257 - Docstring Conventions](https://peps.python.org/pep-0257/)
- [Google Python Style Guide](https://google.github.io/styleguide/pyguide.html)
- [Ruff Documentation](https://docs.astral.sh/ruff/)
- [mypy Documentation](https://mypy.readthedocs.io/)
- [Kivy Documentation](https://kivy.org/doc/stable/)
- [Kivy Event Dispatcher](https://kivy.org/doc/stable/api-kivy.event.html)

---

*"Readability counts."* — The Zen of Python