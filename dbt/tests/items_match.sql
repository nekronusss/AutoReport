select order_id from {{ ref('fact_orders') }} where abs(total_amount-item_amount)>0.01

