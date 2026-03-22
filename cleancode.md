# Clean Code Manifesto

> Basierend auf "Clean Code: A Handbook of Agile Software Craftsmanship" von Robert C. Martin

Dieses Dokument dient als sprachunabhängige Grundlage für Code-Reviews und definiert die allgemeinen Standards für sauberen, wartbaren Code.

**Siehe auch:** [cleancode-python.md](cleancode-python.md) für Python-spezifische Regeln.

---

## Grundprinzipien

### 1. Lesbarkeit vor Cleverness
Code wird häufiger gelesen als geschrieben. Schreibe Code für Menschen, nicht für Maschinen.

### 2. Boy Scout Rule
> "Hinterlasse den Code sauberer, als du ihn vorgefunden hast."

### 3. Keep It Simple, Stupid (KISS)
Einfachheit ist das oberste Ziel. Reduziere Komplexität wo immer möglich.

### 4. Don't Repeat Yourself (DRY)
Jedes Wissen sollte eine einzige, eindeutige Repräsentation im System haben.

### 5. YAGNI - You Aren't Gonna Need It
Implementiere keine Funktionalität, die aktuell nicht benötigt wird.

---

## Bewertungsschema für Code-Reviews

Jedes Finding wird mit einer Severity klassifiziert:
                  
| Severity | Symbol | Bedeutung | Aktion |
|----------|--------|-----------|--------|
| **Kritisch** | 🔴 | Sicherheitslücke, Datenverlust, Crash | Muss vor Merge behoben werden |
| **Wichtig** | 🟠 | Wartbarkeit, Performance, Clean Code Verletzung | Sollte behoben werden |
| **Minor** | 🟡 | Stilistische Verbesserung, Nice-to-have | Kann behoben werden |
| **Hinweis** | ℹ️ | Diskussionspunkt, Alternative | Zur Kenntnisnahme |

---

## Quantitative Metriken

### Schwellwerte

| Metrik | Grenzwert | Kritisch ab |
|--------|-----------|-------------|
| **Zeilenlänge** | Max. 100 Zeichen | > 120 Zeichen |
| **Funktionslänge** | Max. 30 Zeilen | > 50 Zeilen |
| **Klassenlänge** | Max. 200 Zeilen | > 400 Zeilen |
| **Dateilänge** | Max. 500 Zeilen | > 1000 Zeilen |
| **Parameter pro Funktion** | Max. 3 | > 5 |
| **Verschachtelungstiefe** | Max. 3 Ebenen | > 4 Ebenen |
| **Zyklomatische Komplexität** | Max. 10 | > 15 |
| **Methoden pro Klasse** | Max. 20 | > 30 |
| **Instanzvariablen pro Klasse** | Max. 7 | > 10 |

### Testabdeckung

| Bereich | Ziel | Minimum |
|---------|------|---------|
| **Line Coverage** | > 80% | 60% |
| **Branch Coverage** | > 70% | 50% |
| **Critical Paths** | 100% | 100% |

---

## Code-Review Checkliste

### Namensgebung

| Kriterium | Beschreibung |
|-----------|--------------|
| **Aussagekräftig** | Namen beschreiben klar den Zweck |
| **Aussprechbar** | Namen können in Gesprächen verwendet werden |
| **Durchsuchbar** | Keine Einzelbuchstaben-Variablen (außer Schleifenindizes) |
| **Keine Kodierungen** | Keine ungarische Notation, keine Präfixe |
| **Keine Abkürzungen** | `customerAddress` statt `custAddr` |
| **Konsistent** | Gleiche Konzepte verwenden gleiche Begriffe |

**Beispiele:**
```
❌ int d;                     ✅ int elapsedTimeInDays;
❌ getUserInfo()              ✅ getUser()
❌ yyyymmdd                   ✅ currentDate
❌ hp, apts, stscd            ✅ hourlyPayment, appointments, statusCode
```

### Funktionen

| Kriterium | Beschreibung |
|-----------|--------------|
| **Klein** | Maximal 20-30 Zeilen, idealerweise weniger |
| **Eine Aufgabe** | Eine Funktion tut genau eine Sache |
| **Eine Abstraktionsebene** | Nicht High-Level und Low-Level mischen |
| **Wenige Parameter** | Maximal 3, idealerweise 0-2 |
| **Keine Flag-Parameter** | `render(true)` → `renderForSuite()` / `renderForTest()` |
| **Keine Seiteneffekte** | Funktion tut nur, was der Name verspricht |
| **Command-Query-Separation** | Entweder etwas tun ODER etwas zurückgeben |

**Beispiele:**
```
❌ processData(data, format, validate, log)   // zu viele Parameter
✅ parseUserInput(rawInput)                   // eine Aufgabe

❌ checkAndSetPassword(password)              // Command und Query gemischt
✅ isValidPassword(password)                  // Query
✅ setPassword(password)                      // Command
```

### Kommentare

| Kriterium | Beschreibung |
|-----------|--------------|
| **Code erklärt sich selbst** | Guter Code braucht wenige Kommentare |
| **Kein auskommentierter Code** | Versionskontrolle speichert die Historie |
| **Keine redundanten Kommentare** | Nicht wiederholen, was der Code sagt |
| **Keine schließenden Klammer-Kommentare** | `} // end if` ist unnötig |

**Erlaubte Kommentare:**
- Rechtliche Hinweise (Copyright, Lizenz)
- Erklärung der Absicht (WARUM, nicht WAS)
- Warnung vor Konsequenzen
- TODO-Markierungen (temporär)
- Dokumentation öffentlicher APIs

**Beispiele:**
```
❌ i++;  // Erhöhe i um 1

❌ if (user.role == "admin")  // Prüfe ob Admin

✅ // Regex aus RFC 5322 für E-Mail-Validierung
   EMAIL_PATTERN = ...

✅ // ACHTUNG: Timeout muss > 30s sein wegen Legacy-API
   TIMEOUT = 45000;
```

### Formatierung

| Kriterium | Beschreibung |
|-----------|--------------|
| **Vertikale Trennung** | Zusammengehöriger Code steht zusammen |
| **Vertikale Dichte** | Verwandte Konzepte nah beieinander |
| **Horizontale Länge** | Maximal 100-120 Zeichen pro Zeile |
| **Konsistente Einrückung** | Einheitlicher Stil im gesamten Projekt |
| **Keine horizontale Ausrichtung** | Keine Spalten-Ausrichtung von Variablen |

**Beispiele:**
```
❌ name         = "Max";
   age          = 25;
   emailAddress = "max@example.com";

✅ name = "Max";
   age = 25;
   emailAddress = "max@example.com";
```

### Objekte und Klassen

| Kriterium | Beschreibung |
|-----------|--------------|
| **Single Responsibility** | Eine Klasse hat nur einen Grund zur Änderung |
| **Klein** | Wenige Methoden, wenige Instanzvariablen |
| **Kapselung** | Interne Struktur verbergen |
| **Law of Demeter** | Nur mit direkten Nachbarn sprechen |
| **Composition over Inheritance** | Vererbung sparsam einsetzen |

**Law of Demeter - Beispiele:**
```
❌ customer.getAddress().getCity().getName()
✅ customer.getCityName()

❌ order.getCustomer().getPaymentMethod().charge(amount)
✅ order.charge(amount)
```

### Fehlerbehandlung

| Kriterium | Beschreibung |
|-----------|--------------|
| **Exceptions statt Fehlercodes** | Exceptions für Ausnahmefälle |
| **Aussagekräftige Fehlermeldungen** | Kontext und Ursache benennen |
| **Keine null-Rückgaben** | Optional, leere Listen oder Exceptions |
| **Keine null-Parameter** | Defensive Programmierung vermeiden |
| **Fail Fast** | Früh scheitern, nicht Fehler verschleppen |
| **Spezifische Exceptions** | Eigene Exception-Klassen für Domain-Fehler |

**Beispiele:**
```
❌ getUser(id) { if (!id) return null; }

✅ getUser(id) { if (!id) throw new InvalidArgumentError("User ID required"); }

❌ catch (error) { console.log(error); }

✅ catch (error) {
     logger.error("Payment failed", { orderId, error });
     throw new PaymentProcessingError(orderId, error);
   }
```

### Tests

| Kriterium | Beschreibung |
|-----------|--------------|
| **F.I.R.S.T.** | Fast, Independent, Repeatable, Self-Validating, Timely |
| **Ein Konzept pro Test** | Jeder Test prüft eine Sache |
| **Arrange-Act-Assert** | Klare Struktur in jedem Test |
| **Lesbare Testnamen** | `shouldRejectInvalidEmail()` statt `test1()` |
| **Keine Logik in Tests** | Keine Schleifen, keine Bedingungen |

**Beispiele:**
```
❌ test("user", () => { ... })

✅ test("should reject registration with invalid email format", () => {
     // Arrange
     const invalidEmail = "not-an-email";

     // Act
     const result = validateEmail(invalidEmail);

     // Assert
     expect(result.isValid).toBe(false);
   })
```

---

## Code Smells - Warnsignale

### Architektur-Smells

| Smell | Beschreibung | Severity |
|-------|--------------|----------|
| **Rigidität** | Kleine Änderungen erfordern viele Anpassungen | 🟠 |
| **Fragilität** | Änderungen brechen unerwartete Stellen | 🔴 |
| **Immobilität** | Code kann nicht wiederverwendet werden | 🟠 |
| **Viskosität** | Der richtige Weg ist schwerer als der falsche | 🟡 |

### Code-Smells

| Smell | Beschreibung | Severity |
|-------|--------------|----------|
| **Lange Funktionen** | Mehr als 30 Zeilen | 🟠 |
| **Lange Dateien** | Mehr als 500 Zeilen | 🟠 |
| **Lange Parameterlisten** | Mehr als 3 Parameter | 🟡 |
| **Duplikation** | Ähnlicher Code an mehreren Stellen | 🟠 |
| **Feature Envy** | Methode nutzt mehr fremde als eigene Daten | 🟡 |
| **Data Clumps** | Gleiche Variablengruppen wiederholen sich | 🟡 |
| **Primitive Obsession** | Primitive statt Value Objects | 🟡 |
| **Switch Statements** | Oft Zeichen für fehlenden Polymorphismus | 🟡 |
| **Dead Code** | Unerreichbarer oder ungenutzter Code | 🟠 |
| **Spekulativer Code** | "Vielleicht brauchen wir das später" | 🟡 |
| **God Class** | Klasse mit zu vielen Verantwortlichkeiten | 🔴 |
| **String-based Error Handling** | `if "error" in str(e)` | 🟠 |

---

## SOLID Prinzipien

### S - Single Responsibility Principle
> Eine Klasse sollte nur einen Grund zur Änderung haben.

```
❌ Verletzt SRP - User macht zu viel:
   class User {
     saveToDatabase()
     sendEmail()
     generateReport()
   }

✅ SRP eingehalten - getrennte Verantwortlichkeiten:
   class User { ... }
   class UserRepository { ... }
   class EmailService { ... }
   class ReportGenerator { ... }
```

### O - Open/Closed Principle
> Offen für Erweiterung, geschlossen für Modifikation.

Neue Funktionalität durch Hinzufügen von Code, nicht durch Ändern von bestehendem Code.

### L - Liskov Substitution Principle
> Unterklassen müssen ihre Basisklassen ersetzen können.

Subtypen müssen sich so verhalten, dass Code, der mit dem Basistyp arbeitet, auch mit dem Subtyp funktioniert.

### I - Interface Segregation Principle
> Viele spezifische Interfaces sind besser als ein allgemeines.

Clients sollten nicht von Interfaces abhängen, die sie nicht nutzen.

### D - Dependency Inversion Principle
> Abhängigkeiten zeigen auf Abstraktionen, nicht auf Konkretionen.

```
❌ Verletzt DIP - direkte Abhängigkeit:
   class InvoiceService {
     constructor() {
       this.pdfGenerator = new PdfGenerator();  // Konkrete Klasse
     }
   }

✅ DIP eingehalten - Abhängigkeit injiziert:
   class InvoiceService {
     constructor(pdfGenerator: PdfGenerator) {
       this.pdfGenerator = pdfGenerator;
     }
   }
```

---

## Review-Fragen

Bei jedem Code-Review diese Fragen stellen:

### Verständlichkeit
1. Verstehe ich den Code beim ersten Lesen?
2. Sind alle Namen selbsterklärend?
3. Tut jede Funktion nur eine Sache?

### Qualität
4. Gibt es Copy-Paste-Code (Duplikation)?
5. Gibt es eine einfachere Lösung?
6. Sind Fehlerfälle sinnvoll behandelt?
7. Ist der Code testbar und getestet?
8. Sind Abhängigkeiten minimiert und klar?

### Sicherheit
9. Werden alle Eingaben validiert?
10. Werden Queries parametrisiert (SQL Injection)?
11. Sind keine Passwörter/Keys im Code?

### Performance
12. Werden Daten effizient geladen (N+1 Problem)?
13. Werden teure Operationen bei Bedarf gecacht?

---

## Review-Prozess

### Während des Reviews

1. **Erst verstehen** - Code lesen und Zweck verstehen
2. **Metriken prüfen** - Datei-/Funktionslänge, Komplexität
3. **Checkliste durchgehen** - Systematisch alle Punkte prüfen
4. **Findings dokumentieren** - Mit Severity und Begründung

### Finding-Format

```markdown
## 🟠 [Datei:Zeile] Kurze Beschreibung

**Problem:** Was ist das Problem?

**Clean Code Regel:** Welche Regel wird verletzt?

**Vorschlag:**
// Verbesserter Code
```

---

## Quellen

- Martin, Robert C. *Clean Code: A Handbook of Agile Software Craftsmanship*. Prentice Hall, 2008.
- Martin, Robert C. *The Clean Coder*. Prentice Hall, 2011.
- Martin, Robert C. *Clean Architecture*. Prentice Hall, 2017.
- [Summary of Clean Code (GitHub Gist)](https://gist.github.com/wojteklu/73c6914cc446146b8b533c0988cf8d29)

---

*"Any fool can write code that a computer can understand. Good programmers write code that humans can understand."* — Martin Fowler