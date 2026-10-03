select order_id from {{ ref('fact_orders') }} where refunded_amount>captured_amount
