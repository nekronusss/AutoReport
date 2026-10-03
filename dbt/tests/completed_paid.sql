select order_id from {{ ref('fact_orders') }}
where status='completed' and abs(total_amount-net_collected_amount)>0.01

