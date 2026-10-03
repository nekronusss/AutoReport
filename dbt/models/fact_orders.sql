with items as (
 select order_id, sum(quantity * unit_price)::numeric(18,2) item_amount
 from {{ target.schema }}.order_items group by order_id
), payments as (
 select order_id,
 coalesce(sum(amount) filter (where status='succeeded' and kind='payment'),0)::numeric(18,2) captured_amount,
 coalesce(sum(amount) filter (where status='succeeded' and kind='refund'),0)::numeric(18,2) refunded_amount
 from {{ target.schema }}.payments group by order_id
)
select o.order_id,o.customer_id,(o.created_at at time zone '{{ var("timezone") }}')::date business_date,
o.status,o.currency,o.total_amount,coalesce(i.item_amount,0)::numeric(18,2) item_amount,
coalesce(p.captured_amount,0)::numeric(18,2) captured_amount,
coalesce(p.refunded_amount,0)::numeric(18,2) refunded_amount,
(coalesce(p.captured_amount,0)-coalesce(p.refunded_amount,0))::numeric(18,2) net_collected_amount
from {{ target.schema }}.orders o left join items i using(order_id) left join payments p using(order_id)

