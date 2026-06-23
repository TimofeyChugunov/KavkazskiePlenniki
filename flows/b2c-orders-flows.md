# Flow B2C-10: Просмотр и отслеживание заказов

## GET /api/v1/orders — Список заказов пользователя

```
Покупатель                B2C
    │                      │
    │  GET /api/v1/orders  │
    │  ?limit=20&offset=0  │
    │  &status=PAID        │
    │ ────────────────────>│
    │                      │  1. JWT → user_id
    │                      │  2. Фильтр по user_id
    │                      │  3. Фильтр по status (опционально)
    │  200 {items, ...}    │
    │ <────────────────────│
```

### Поведение
- user_id извлекается **только** из JWT claims
- Возвращаются только заказы текущего пользователя
- В списке items **не разворачиваются** — только items_count
- Поддерживается пагинация (limit, offset) и фильтр по статусу

### Response 200
```json
{
  "items": [
    {
      "id": "...",
      "status": "PAID",
      "total_amount": 38997000,
      "items_count": 2,
      "created_at": "...",
      "updated_at": "..."
    }
  ],
  "total_count": 2,
  "limit": 20,
  "offset": 0
}
```

## GET /api/v1/orders/{id} — Детали заказа

```
Покупатель                B2C
    │                      │
    │  GET /api/v1/orders/ │
    │  {id}                │
    │ ────────────────────>│
    │                      │  1. JWT → user_id
    │                      │  2. Поиск заказа по id
    │                      │  3. Проверка: order.user_id == jwt.user_id?
    │                      │     Нет → 404 (не 403!)
    │  200 {order}         │
    │ <────────────────────│
```

### Поведение
- user_id извлекается **только** из JWT claims
- Чужой заказ → **404** (не 403), чтобы не раскрывать существование
- Цены берутся из OrderItem (unit_price), не из текущего SKU в B2B

### Response 200
```json
{
  "id": "...",
  "status": "PAID",
  "items": [
    {
      "id": "...",
      "sku_id": "...",
      "product_id": "...",
      "product_title": "iPhone 15 Pro Max",
      "sku_name": "256GB Black",
      "quantity": 2,
      "unit_price": 12999000,
      "line_total": 25998000
    }
  ],
  "total_amount": 38997000,
  "delivery_address": "...",
  "created_at": "...",
  "updated_at": "..."
}
```

### Response 404
```json
{"code": "ORDER_NOT_FOUND", "message": "Заказ не найден"}
```

## Коды ответов

| Endpoint | Код | Описание |
|----------|-----|----------|
| GET /orders | 200 | Список заказов |
| GET /orders | 400 | Невалидный фильтр статуса |
| GET /orders | 401 | Требуется авторизация |
| GET /orders/{id} | 200 | Детали заказа |
| GET /orders/{id} | 401 | Требуется авторизация |
| GET /orders/{id} | 404 | Заказ не найден (или чужой) |
