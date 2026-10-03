select '{{ var("period") }}'::date business_date, '{{ var("currency") }}'::text currency,
count(*)::bigint orders_count,count(distinct customer_id)::bigint customers_count,
coalesce(sum(total_amount) filter (where status <> 'cancelled'),0)::numeric(18,2) ordered_amount,
coalesce(sum(captured_amount),0)::numeric(18,2) captured_amount,
coalesce(sum(refunded_amount),0)::numeric(18,2) refunded_amount,
coalesce(sum(net_collected_amount),0)::numeric(18,2) net_collected_amount
from {{ ref('fact_orders') }}

