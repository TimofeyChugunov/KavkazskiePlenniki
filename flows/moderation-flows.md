# Flow MOD-3: Одобрение товара

## POST /api/v1/tickets/{ticket_id}/approve — Одобрить тикет

```
Модератор            Moderation                    B2B
    │                    │                           │
    │  POST /tickets/    │                           │
    │  {id}/approve      │                           │
    │ ──────────────────>│                           │
    │                    │                           │
    │                    │  1. Проверка:             │
    │                    │     - тикет существует?   │
    │                    │     - status=IN_REVIEW?   │
    │                    │     - moderator_id=me?    │
    │                    │     - status≠HARD_BLOCKED?│
    │                    │                           │
    │                    │  2. GET /products/{id}    │
    │                    │     → проверить SKU       │
    │                    │ ─────────────────────────>│
    │                    │     product + skus        │
    │                    │ <─────────────────────────│
    │                    │                           │
    │                    │  3. UPDATE ticket:        │
    │                    │     status = APPROVED     │
    │                    │     decision_at = now()   │
    │                    │     clear field_reports   │
    │                    │                           │
    │                    │  4. POST /events/moderation│
    │                    │     {status: MODERATED}   │
    │                    │ ─────────────────────────>│
    │                    │                           │  product.status →
    │                    │            200 OK         │  MODERATED
    │                    │ <─────────────────────────│  blocked = false
    │  200 OK            │                           │
    │ ◄──────────────────│                           │
```

### Предусловия
- Тикет существует в системе модерации
- status = IN_REVIEW (модератор должен сначала взять тикет через claim)
- assigned_moderator_id = текущий модератор (нельзя одобрить чужой тикет)
- status != HARD_BLOCKED (терминальный статус — отменить нельзя)

### Логика
1. Найти тикет по ticket_id → 404 если не найден
2. Если status = HARD_BLOCKED → 409 "Product is permanently blocked"
3. Если status != IN_REVIEW → 409 "Product is not in review status"
4. Если assigned_moderator_id != текущий модератор → 403 "Not assigned to you"
5. GET {b2b_url}/api/v1/products/{product_id} — проверить что у товара есть SKU
   - Если 0 SKU → 409 "Product has no SKUs, cannot approve"
6. UPDATE ticket: status=APPROVED, decision_at=now(), decision_comment, clear field_reports
7. DELETE field_reports по тикету
8. POST {b2b_url}/api/v1/events/moderation {product_id, status: MODERATED}
9. Если B2B вернул ошибку → логировать, 500 модератору (статус тикета остаётся IN_REVIEW)
10. Ответ 200 OK

### Исходящее событие (Moderation → B2B)

```
POST {b2b_url}/api/v1/events/moderation
Content-Type: application/json
X-Service-Key: {mod_to_b2b_key}

{
  "idempotency_key": "...",
  "product_id": "...",
  "status": "MODERATED"
}
```

B2B при получении:
- product.status → MODERATED
- product.blocked → false
- Товар становится доступен в каталоге (при наличии SKU с activeQuantity > 0)

### Response 200
```json
{
  "product_id": "...",
  "status": "APPROVED"
}
```

### Response 403
```json
{"code": "NOT_ASSIGNED", "message": "This moderation card is not assigned to you"}
```

### Response 404
```json
{"code": "TICKET_NOT_FOUND", "message": "Тикет не найден"}
```

### Response 409
```json
{"code": "TICKET_WRONG_STATUS", "message": "Product is not in review status"}
```
или
```json
{"code": "NO_SKUS", "message": "Product has no SKUs, cannot approve"}
```
или
```json
{"code": "PRODUCT_PERMANENTLY_BLOCKED", "message": "Product is permanently blocked"}
```

### Коды ответов

| Endpoint | Код | Описание |
|----------|-----|----------|
| POST /tickets/{id}/approve | 200 | Тикет одобрен, событие MODERATED отправлено |
| POST /tickets/{id}/approve | 401 | Требуется авторизация |
| POST /tickets/{id}/approve | 403 | Тикет закреплён за другим модератором |
| POST /tickets/{id}/approve | 404 | Тикет не найден |
| POST /tickets/{id}/approve | 409 | Неверный статус / товар без SKU / HARD_BLOCKED |
| POST /tickets/{id}/approve | 500 | B2B недоступен |
