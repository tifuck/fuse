-- /sources sums each pool account's events per card (estimated cost and
-- priced/unpriced counts). With only credential_id indexed, every card read
-- its events row by row; covering the summed columns makes it an index-only
-- scan. Same index name, so app/db_schema's existence check passes before
-- and after. Online: concurrent usage writes continue during the rebuild.
ALTER TABLE usage_events
  DROP INDEX usage_events_credential,
  ADD INDEX usage_events_credential (credential_id, pricing_known, total_tokens, estimated_cost_micros),
  ALGORITHM=INPLACE, LOCK=NONE;
