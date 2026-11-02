## ADDED Requirements

### Requirement: Paid payment message triggers delivery

The system SHALL trigger PPT card delivery from the WebSocket payment message (`contentType=26`, “已付款待发货”) without requiring the seller order-list API.

#### Scenario: Type 26 payment message received

- **WHEN** the active seller account receives a WebSocket message with `contentType=26` and a paid marker such as “等待你发货” or “已付款”
- **THEN** the system SHALL match the goods delivery rule, claim exactly one unused card from the bound card group, create a delivery record, and send the redeem link to the buyer

#### Scenario: Seller order API is unavailable

- **WHEN** the seller order-list API returns a permission or session error
- **THEN** a paid `contentType=26` message SHALL still be able to trigger delivery without waiting for order synchronization

### Requirement: Delivery requires an online seller session

The system MUST only deliver when the seller account WebSocket session is connected and the account is enabled.

#### Scenario: WebSocket session is offline

- **WHEN** the seller account session is expired or the WebSocket is disconnected
- **THEN** the system MUST NOT claim a card or send a delivery message, and the order SHALL remain eligible for manual delivery or later retry

### Requirement: Manual delivery fallback

The system SHALL provide a manual delivery path for paid orders when automatic delivery cannot run.

#### Scenario: Operator manually delivers

- **WHEN** an operator triggers manual card delivery for a paid order using the bound card group
- **THEN** the system SHALL claim one card, create a delivery record, and send the configured template to the buyer without requiring seller-center order synchronization

### Requirement: One enabled account per external UID

The system MUST keep at most one enabled local account record for the same Xianyu `externalUid` to avoid competing session refreshes.

#### Scenario: Duplicate account detected

- **WHEN** the same `externalUid` exists under multiple tenants
- **THEN** only the account selected for the trial SHALL be enabled, and the duplicate record MUST be disabled before the trial login is refreshed

### Requirement: Delivery is idempotent

The system SHALL avoid duplicate card claims and duplicate delivery messages for the same paid order or payment event.

#### Scenario: Repeated payment message

- **WHEN** the same payment event is received more than once within the deduplication window
- **THEN** the system SHALL skip the repeated event when a matching delivery record already exists

### Requirement: No platform-permission bypass

The system MUST NOT bypass seller-center permissions through forged requests, direct database writes, or unauthorized scraping.

#### Scenario: Missing seller-center permission

- **WHEN** the seller account lacks the permission required by `merchant.sold.get`
- **THEN** the system SHALL report the fallback condition and continue to support the WebSocket and manual-delivery paths without attempting a permission bypass
