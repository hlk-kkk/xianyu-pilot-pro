## ADDED Requirements

### Requirement: Redeem link accepts a code without prior cookies

The redeem endpoint SHALL accept `GET /r/{code}` using only the redeem code and SHALL NOT require an existing browser session.

#### Scenario: First visit with no cookies

- **WHEN** a buyer opens `/r/{code}` without any existing `ppt_gate_session` or `presenton_session` cookie
- **THEN** the service SHALL validate the code, perform any required Presenton provisioning and login, and respond with a redirect that sets both session cookies

### Requirement: Workspace requires the issued session cookies

The service MUST require the issued gate and Presenton session cookies for subsequent workspace and proxy requests.

#### Scenario: Cookies preserved

- **WHEN** the browser preserves both cookies issued by the redeem response and follows the redirect to `/`
- **THEN** the service SHALL allow access to the Presenton workspace

#### Scenario: Cookies lost

- **WHEN** the browser drops the issued cookies and requests `/`
- **THEN** the service MUST return a clear “需要兑换码/请用浏览器打开” guidance page rather than silently looping or leaving the user on an unexplained 401

### Requirement: Browser compatibility guidance

The redeem experience SHALL provide guidance for in-app browsers and cookie-blocked environments.

#### Scenario: In-app browser drops cookies

- **WHEN** the request originates from a WebView or browser that does not persist the issued cookies
- **THEN** the user SHALL be instructed to reopen the same link in Safari or Chrome and to clear site data if the loop persists

### Requirement: Redeem is re-entry safe

Reopening the same redeem link SHALL map to the same Presenton user and SHALL NOT create a duplicate account.

#### Scenario: Link opened twice

- **WHEN** an active card’s redeem link is opened again
- **THEN** the service SHALL log in the already-mapped Presenton user and issue fresh session cookies without creating another user

### Requirement: Redeem failures are diagnosable

The service SHALL record enough non-sensitive context to distinguish invalid code, revoked/expired code, Presenton provisioning failure, and cookie handoff failure.

#### Scenario: Presenton login fails

- **WHEN** Presenton provisioning or login fails during redeem
- **THEN** the service SHALL log the card identifier/prefix and failure category without logging cookies, passwords, or full redeem codes
