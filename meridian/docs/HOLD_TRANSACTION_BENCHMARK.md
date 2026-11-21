# Aurora PostgreSQL connection methods for a two-place hold

How long does it take to commit a new hold on an existing journey? On November 21, 2026, we ran the same Meridian transaction through three connections to one Amazon Aurora PostgreSQL writer. Each successful transaction reserved two places and stored its request and audit record.

## Time from begin through commit

Each row covers 312 warm holds across three rounds. Times are milliseconds as observed by the client. The p95 is the time by which 95 percent of those holds had committed.

| Concurrent callers | Connection method | Median (ms) | p95 (ms) | Round p95 range (ms) | Completed holds/s |
| ---: | --- | ---: | ---: | ---: | ---: |
| 1 | RDS Data API | 1066.1 | 1175.7 | 1143.7 to 1189.1 | 0.9 |
| 1 | RDS Proxy | 120.2 | 145.1 | 117.5 to 151.5 | 8.2 |
| 1 | Direct PostgreSQL | 42.9 | 56.6 | 51.7 to 60.0 | 21.4 |
| 8 | RDS Data API | 1074.5 | 1264.2 | 1186.3 to 2671.3 | 6.8 |
| 8 | RDS Proxy | 170.0 | 271.2 | 224.2 to 280.8 | 40.6 |
| 8 | Direct PostgreSQL | 151.6 | 485.6 | 220.4 to 620.9 | 39.8 |

With one warm caller, the median was 1.07 seconds over the Data API, 120 milliseconds through RDS Proxy, and 43 milliseconds directly. That ordering held in all three rounds. With eight callers, direct PostgreSQL had the lower pooled median (152 ms), while RDS Proxy had the lower pooled p95 (271 ms versus 486 ms). Their median ordering changed in one round. The eight-caller Data API p95 also varied, reaching 2.67 seconds in one round. The ranges show what repeating the test revealed.

## What ran

- One Aurora PostgreSQL 18.6 writer in Northern Virginia, fixed at 2 Aurora Capacity Units. Each method used a 1 GB x86 AWS Lambda client running Python 3.13 in the same subnet and Availability Zone as the writer. RDS Proxy used its default writer endpoint across two subnets. The Data API used a private interface endpoint.
- All three used the same workload login and production hold handler. The fixture supplied the verified identity and approved terms that the Gateway normally passes to the handler. Every new hold followed the same ordered sequence of 19 SQL executions between begin and commit. That is 21 Data API requests. The PostgreSQL driver can exchange several protocol messages for a single SQL execution. [Tested Holds Lambda source](https://github.com/aws-samples/sample-stateful-agentic-ai-workflows-aurora-mcp-agentcore/blob/f741b96a80f38f586b45a0fc8f37d385e6d37818/meridian/meridian_agentcore/agentcore/gateway_targets/meridian_holds/lambda_function.py)
- The Data API client used the handler’s JSON results and single-attempt SDK configuration. The two PostgreSQL clients used pg8000 1.31.5 over verified TLS, without batching or pipelining. Each worker reused its client for one batch and closed it afterwards. Connection setup, credential retrieval and Lambda initialization occurred before the timed interval. We timed from immediately before begin until the client received the commit acknowledgement. Model and Gateway calls were outside that interval.
- Each round rotated the order of the three methods. With one caller, the client repeated holds on one package, each on a fresh journey. With eight callers, each client used its own package. Every client waited for its transaction to complete before starting another. We excluded one complete warm-up hold per client per batch. The fixture was verified and reset between configurations.
- All 1,872 timed transactions committed. After each batch we checked the request, booking, quantity, remaining inventory, and linked action and access audit rows. A separate replay returned the same booking and preserved inventory. After a transaction ended, a second traveler on the same client saw none of the first traveler’s private journey rows.
- In a separate last-two-places test for each method, eight transactions overlapped on one package. In all three tests, exactly one two-place hold committed and seven requests were refused for insufficient inventory. These refusals are reported separately from the latency sample.

## Working with the measurements

The [individual transaction times](benchmarks/hold-transaction-latencies.csv) identify the method, round, client count, lane and sample ordinal. Each round contributed 104 measured holds per method and client count. The median uses the midpoint of the two central observations. The p95 uses the nearest-rank observation. Completed holds per second divides completed samples by the sum of the three measured batch windows.

To recalculate the median and p95, open psql from the repository root in a scratch database and run the following commands. The table lasts for the session.

```sql
CREATE TEMP TABLE hold_times (
    round_no integer, clients integer, transport text,
    lane integer, sample_no integer, transaction_ms numeric
);
\copy hold_times FROM 'meridian/docs/benchmarks/hold-transaction-latencies.csv' CSV HEADER
SELECT clients, transport, count(*) AS holds,
       round(percentile_cont(0.5) WITHIN GROUP (ORDER BY transaction_ms)::numeric, 1) AS median_ms,
       round(percentile_disc(0.95) WITHIN GROUP (ORDER BY transaction_ms), 1) AS p95_ms
FROM hold_times
GROUP BY clients, transport
ORDER BY clients, transport;
```

This experiment answers a question about warm, committed holds on a small synthetic database at two specified concurrency levels. Pool sharing, cold connections, reader endpoints, failover, and maximum throughput each need their own workload. For example, Meridian uses transaction-local traveler settings with `set_config(..., true)`, which AWS documents as a pinning trigger for RDS Proxy on PostgreSQL. [Aurora guide to RDS Proxy pinning](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/rds-proxy-pinning.html)
