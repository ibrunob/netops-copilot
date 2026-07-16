-- Local demonstration data only. It is written to the real development
-- database and follows the same case/event/outbox invariants as the API.
-- The file is idempotent: it only creates its fixed demo records once.
BEGIN;

DO $$
DECLARE
  organization uuid := '3b3d76e6-9194-4dfe-9c9d-21b68d7e8f6b';
  actor uuid;
BEGIN
  SELECT id INTO actor
  FROM users
  WHERE oidc_subject = '863aad4c-ba8a-4667-b759-f3deaf52ca67';

  IF actor IS NULL THEN
    RAISE EXCEPTION 'The local demo operator has not been provisioned. Sign in once before running make seed.';
  END IF;

  INSERT INTO assets (id, organization_id, name, environment)
  VALUES
    ('11111111-1111-4111-8111-111111111111', organization, 'mad-core-01', 'production'),
    ('22222222-2222-4222-8222-222222222222', organization, 'bar-edge-02', 'production'),
    ('33333333-3333-4333-8333-333333333333', organization, 'val-warehouse-vpn', 'warehouse')
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO cases (
    id, organization_id, asset_id, title, category, severity, state, version,
    idempotency_key, request_sha256, created_by_actor_id, correlation_id, created_at, updated_at
  ) VALUES
    ('10000000-0000-4000-8000-000000000001', organization, NULL, 'Madrid core: intermittent OSPF adjacency resets', 'routing', 'critical', 'new', 0, 'demo-madrid-ospf', repeat('a', 64), actor, '50000000-0000-4000-8000-000000000001', TIMESTAMPTZ '2026-07-16 16:05:00+00', TIMESTAMPTZ '2026-07-16 16:05:00+00'),
    ('10000000-0000-4000-8000-000000000002', organization, NULL, 'Barcelona edge: IKEv2 Phase 2 lifetime drift', 'ipsec', 'high', 'new', 0, 'demo-barcelona-ipsec', repeat('b', 64), actor, '50000000-0000-4000-8000-000000000002', TIMESTAMPTZ '2026-07-16 15:20:00+00', TIMESTAMPTZ '2026-07-16 15:20:00+00'),
    ('10000000-0000-4000-8000-000000000003', organization, NULL, 'Customer request: firewall inbound NAT', 'customer_request', 'medium', 'new', 0, 'demo-firewall-inbound-nat', repeat('c', 64), actor, '50000000-0000-4000-8000-000000000003', TIMESTAMPTZ '2026-07-16 14:10:00+00', TIMESTAMPTZ '2026-07-16 14:10:00+00'),
    ('10000000-0000-4000-8000-000000000004', organization, NULL, 'Madrid core: BGP policy change awaiting review', 'routing', 'high', 'new', 0, 'demo-madrid-bgp', repeat('d', 64), actor, '50000000-0000-4000-8000-000000000004', TIMESTAMPTZ '2026-07-16 12:30:00+00', TIMESTAMPTZ '2026-07-16 12:30:00+00'),
    ('10000000-0000-4000-8000-000000000005', organization, NULL, 'Barcelona edge: maintenance validation ready', 'maintenance', 'low', 'new', 0, 'demo-barcelona-maintenance', repeat('e', 64), actor, '50000000-0000-4000-8000-000000000005', TIMESTAMPTZ '2026-07-16 10:00:00+00', TIMESTAMPTZ '2026-07-16 10:00:00+00')
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO case_events (
    id, organization_id, case_id, transition_id, event_type, aggregate_version,
    actor_id, correlation_id, payload, occurred_at
  ) VALUES
    ('20000000-0000-4000-8000-000000000001', organization, '10000000-0000-4000-8000-000000000001', NULL, 'case.created.v1', 0, actor, '50000000-0000-4000-8000-000000000001', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 16:05:00+00'),
    ('20000000-0000-4000-8000-000000000002', organization, '10000000-0000-4000-8000-000000000002', NULL, 'case.created.v1', 0, actor, '50000000-0000-4000-8000-000000000002', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 15:20:00+00'),
    ('20000000-0000-4000-8000-000000000003', organization, '10000000-0000-4000-8000-000000000003', NULL, 'case.created.v1', 0, actor, '50000000-0000-4000-8000-000000000003', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 14:10:00+00'),
    ('20000000-0000-4000-8000-000000000004', organization, '10000000-0000-4000-8000-000000000004', NULL, 'case.created.v1', 0, actor, '50000000-0000-4000-8000-000000000004', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 12:30:00+00'),
    ('20000000-0000-4000-8000-000000000005', organization, '10000000-0000-4000-8000-000000000005', NULL, 'case.created.v1', 0, actor, '50000000-0000-4000-8000-000000000005', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 10:00:00+00')
  ON CONFLICT (id) DO NOTHING;

  INSERT INTO outbox_events (
    id, organization_id, case_id, case_event_id, event_type, aggregate_version,
    correlation_id, payload, available_at, published_at, created_at
  ) VALUES
    ('40000000-0000-4000-8000-000000000001', organization, '10000000-0000-4000-8000-000000000001', '20000000-0000-4000-8000-000000000001', 'case.created.v1', 0, '50000000-0000-4000-8000-000000000001', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 16:05:00+00', TIMESTAMPTZ '2026-07-16 16:05:00+00', TIMESTAMPTZ '2026-07-16 16:05:00+00'),
    ('40000000-0000-4000-8000-000000000002', organization, '10000000-0000-4000-8000-000000000002', '20000000-0000-4000-8000-000000000002', 'case.created.v1', 0, '50000000-0000-4000-8000-000000000002', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 15:20:00+00', TIMESTAMPTZ '2026-07-16 15:20:00+00', TIMESTAMPTZ '2026-07-16 15:20:00+00'),
    ('40000000-0000-4000-8000-000000000003', organization, '10000000-0000-4000-8000-000000000003', '20000000-0000-4000-8000-000000000003', 'case.created.v1', 0, '50000000-0000-4000-8000-000000000003', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 14:10:00+00', TIMESTAMPTZ '2026-07-16 14:10:00+00', TIMESTAMPTZ '2026-07-16 14:10:00+00'),
    ('40000000-0000-4000-8000-000000000004', organization, '10000000-0000-4000-8000-000000000004', '20000000-0000-4000-8000-000000000004', 'case.created.v1', 0, '50000000-0000-4000-8000-000000000004', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 12:30:00+00', TIMESTAMPTZ '2026-07-16 12:30:00+00', TIMESTAMPTZ '2026-07-16 12:30:00+00'),
    ('40000000-0000-4000-8000-000000000005', organization, '10000000-0000-4000-8000-000000000005', '20000000-0000-4000-8000-000000000005', 'case.created.v1', 0, '50000000-0000-4000-8000-000000000005', '{}'::jsonb, TIMESTAMPTZ '2026-07-16 10:00:00+00', TIMESTAMPTZ '2026-07-16 10:00:00+00', TIMESTAMPTZ '2026-07-16 10:00:00+00')
  ON CONFLICT (id) DO NOTHING;

  -- Give the queue a representative operational spread while preserving the
  -- same append-only transition/event/outbox chain the API writes. Case one
  -- intentionally remains new so an operator can demo the full workflow.
  INSERT INTO case_transitions (
    id, organization_id, case_id, from_state, to_state, version, actor_id,
    actor_kind, correlation_id, occurred_at, approval_id, verification_note,
    knowledge_item_id, note
  ) VALUES
    ('30000000-0000-4000-8000-000000000001', organization, '10000000-0000-4000-8000-000000000002', 'new', 'investigating', 1, actor, 'human', '50000000-0000-4000-8000-000000000021', TIMESTAMPTZ '2026-07-16 15:28:00+00', NULL, NULL, NULL, 'IKE proposal comparison started.'),
    ('30000000-0000-4000-8000-000000000002', organization, '10000000-0000-4000-8000-000000000003', 'new', 'investigating', 1, actor, 'human', '50000000-0000-4000-8000-000000000031', TIMESTAMPTZ '2026-07-16 14:16:00+00', NULL, NULL, NULL, 'Initial tunnel telemetry reviewed.'),
    ('30000000-0000-4000-8000-000000000003', organization, '10000000-0000-4000-8000-000000000003', 'investigating', 'needs_information', 2, actor, 'human', '50000000-0000-4000-8000-000000000032', TIMESTAMPTZ '2026-07-16 14:32:00+00', NULL, NULL, NULL, 'Please attach the peer gateway phase-two logs for the affected interval.'),
    ('30000000-0000-4000-8000-000000000004', organization, '10000000-0000-4000-8000-000000000004', 'new', 'investigating', 1, actor, 'human', '50000000-0000-4000-8000-000000000041', TIMESTAMPTZ '2026-07-16 12:38:00+00', NULL, NULL, NULL, 'Policy diff and route impact assessment started.'),
    ('30000000-0000-4000-8000-000000000005', organization, '10000000-0000-4000-8000-000000000004', 'investigating', 'diagnosed', 2, actor, 'human', '50000000-0000-4000-8000-000000000042', TIMESTAMPTZ '2026-07-16 12:50:00+00', NULL, NULL, NULL, 'Community match order suppresses the intended backup route.'),
    ('30000000-0000-4000-8000-000000000006', organization, '10000000-0000-4000-8000-000000000004', 'diagnosed', 'fix_proposed', 3, actor, 'human', '50000000-0000-4000-8000-000000000043', TIMESTAMPTZ '2026-07-16 13:05:00+00', NULL, NULL, NULL, 'Proposed policy order correction is ready for change review.'),
    ('30000000-0000-4000-8000-000000000007', organization, '10000000-0000-4000-8000-000000000004', 'fix_proposed', 'confirmed', 4, actor, 'human', '50000000-0000-4000-8000-000000000044', TIMESTAMPTZ '2026-07-16 13:20:00+00', '60000000-0000-4000-8000-000000000004', NULL, NULL, 'Change reviewer approved the scoped policy correction.'),
    ('30000000-0000-4000-8000-000000000008', organization, '10000000-0000-4000-8000-000000000005', 'new', 'investigating', 1, actor, 'human', '50000000-0000-4000-8000-000000000051', TIMESTAMPTZ '2026-07-16 10:08:00+00', NULL, NULL, NULL, 'Post-maintenance checks started.'),
    ('30000000-0000-4000-8000-000000000009', organization, '10000000-0000-4000-8000-000000000005', 'investigating', 'diagnosed', 2, actor, 'human', '50000000-0000-4000-8000-000000000052', TIMESTAMPTZ '2026-07-16 10:16:00+00', NULL, NULL, NULL, 'No forwarding anomalies remain after maintenance.'),
    ('30000000-0000-4000-8000-000000000010', organization, '10000000-0000-4000-8000-000000000005', 'diagnosed', 'fix_proposed', 3, actor, 'human', '50000000-0000-4000-8000-000000000053', TIMESTAMPTZ '2026-07-16 10:24:00+00', NULL, NULL, NULL, 'No corrective change required; validation plan proposed.'),
    ('30000000-0000-4000-8000-000000000011', organization, '10000000-0000-4000-8000-000000000005', 'fix_proposed', 'confirmed', 4, actor, 'human', '50000000-0000-4000-8000-000000000054', TIMESTAMPTZ '2026-07-16 10:34:00+00', '60000000-0000-4000-8000-000000000005', NULL, NULL, 'Maintenance validation accepted by the change reviewer.'),
    ('30000000-0000-4000-8000-000000000012', organization, '10000000-0000-4000-8000-000000000005', 'confirmed', 'resolved', 5, actor, 'human', '50000000-0000-4000-8000-000000000055', TIMESTAMPTZ '2026-07-16 10:45:00+00', NULL, 'Validated BGP convergence and application reachability from both edge probes.', NULL, 'Verification evidence recorded after the maintenance window.')
  ON CONFLICT DO NOTHING;

  INSERT INTO case_events (
    id, organization_id, case_id, transition_id, event_type, aggregate_version,
    actor_id, correlation_id, payload, occurred_at
  )
  SELECT
    event_id, organization, case_id, transition_id, event_type, aggregate_version,
    actor, correlation_id, '{}'::jsonb, occurred_at
  FROM (VALUES
    ('20000000-0000-4000-8000-000000000021'::uuid, '10000000-0000-4000-8000-000000000002'::uuid, '30000000-0000-4000-8000-000000000001'::uuid, 'case.investigating.v1', 1, '50000000-0000-4000-8000-000000000021'::uuid, TIMESTAMPTZ '2026-07-16 15:28:00+00'),
    ('20000000-0000-4000-8000-000000000031'::uuid, '10000000-0000-4000-8000-000000000003'::uuid, '30000000-0000-4000-8000-000000000002'::uuid, 'case.investigating.v1', 1, '50000000-0000-4000-8000-000000000031'::uuid, TIMESTAMPTZ '2026-07-16 14:16:00+00'),
    ('20000000-0000-4000-8000-000000000032'::uuid, '10000000-0000-4000-8000-000000000003'::uuid, '30000000-0000-4000-8000-000000000003'::uuid, 'case.needs_information.v1', 2, '50000000-0000-4000-8000-000000000032'::uuid, TIMESTAMPTZ '2026-07-16 14:32:00+00'),
    ('20000000-0000-4000-8000-000000000041'::uuid, '10000000-0000-4000-8000-000000000004'::uuid, '30000000-0000-4000-8000-000000000004'::uuid, 'case.investigating.v1', 1, '50000000-0000-4000-8000-000000000041'::uuid, TIMESTAMPTZ '2026-07-16 12:38:00+00'),
    ('20000000-0000-4000-8000-000000000042'::uuid, '10000000-0000-4000-8000-000000000004'::uuid, '30000000-0000-4000-8000-000000000005'::uuid, 'analysis.completed.v1', 2, '50000000-0000-4000-8000-000000000042'::uuid, TIMESTAMPTZ '2026-07-16 12:50:00+00'),
    ('20000000-0000-4000-8000-000000000043'::uuid, '10000000-0000-4000-8000-000000000004'::uuid, '30000000-0000-4000-8000-000000000006'::uuid, 'recommendation.proposed.v1', 3, '50000000-0000-4000-8000-000000000043'::uuid, TIMESTAMPTZ '2026-07-16 13:05:00+00'),
    ('20000000-0000-4000-8000-000000000044'::uuid, '10000000-0000-4000-8000-000000000004'::uuid, '30000000-0000-4000-8000-000000000007'::uuid, 'approval.granted.v1', 4, '50000000-0000-4000-8000-000000000044'::uuid, TIMESTAMPTZ '2026-07-16 13:20:00+00'),
    ('20000000-0000-4000-8000-000000000051'::uuid, '10000000-0000-4000-8000-000000000005'::uuid, '30000000-0000-4000-8000-000000000008'::uuid, 'case.investigating.v1', 1, '50000000-0000-4000-8000-000000000051'::uuid, TIMESTAMPTZ '2026-07-16 10:08:00+00'),
    ('20000000-0000-4000-8000-000000000052'::uuid, '10000000-0000-4000-8000-000000000005'::uuid, '30000000-0000-4000-8000-000000000009'::uuid, 'analysis.completed.v1', 2, '50000000-0000-4000-8000-000000000052'::uuid, TIMESTAMPTZ '2026-07-16 10:16:00+00'),
    ('20000000-0000-4000-8000-000000000053'::uuid, '10000000-0000-4000-8000-000000000005'::uuid, '30000000-0000-4000-8000-000000000010'::uuid, 'recommendation.proposed.v1', 3, '50000000-0000-4000-8000-000000000053'::uuid, TIMESTAMPTZ '2026-07-16 10:24:00+00'),
    ('20000000-0000-4000-8000-000000000054'::uuid, '10000000-0000-4000-8000-000000000005'::uuid, '30000000-0000-4000-8000-000000000011'::uuid, 'approval.granted.v1', 4, '50000000-0000-4000-8000-000000000054'::uuid, TIMESTAMPTZ '2026-07-16 10:34:00+00'),
    ('20000000-0000-4000-8000-000000000055'::uuid, '10000000-0000-4000-8000-000000000005'::uuid, '30000000-0000-4000-8000-000000000012'::uuid, 'case.resolved.v1', 5, '50000000-0000-4000-8000-000000000055'::uuid, TIMESTAMPTZ '2026-07-16 10:45:00+00')
  ) AS demo(event_id, case_id, transition_id, event_type, aggregate_version, correlation_id, occurred_at)
  ON CONFLICT DO NOTHING;

  INSERT INTO outbox_events (
    id, organization_id, case_id, case_event_id, event_type, aggregate_version,
    correlation_id, payload, available_at, published_at, created_at
  )
  SELECT
    ('40000000-0000-4000-8000-' || right(event.id::text, 12))::uuid,
    organization, event.case_id, event.id, event.event_type, event.aggregate_version,
    event.correlation_id, '{}'::jsonb, event.occurred_at, event.occurred_at, event.occurred_at
  FROM case_events AS event
  WHERE event.id IN (
      '20000000-0000-4000-8000-000000000021', '20000000-0000-4000-8000-000000000031',
      '20000000-0000-4000-8000-000000000032', '20000000-0000-4000-8000-000000000041',
      '20000000-0000-4000-8000-000000000042', '20000000-0000-4000-8000-000000000043',
      '20000000-0000-4000-8000-000000000044', '20000000-0000-4000-8000-000000000051',
      '20000000-0000-4000-8000-000000000052', '20000000-0000-4000-8000-000000000053',
      '20000000-0000-4000-8000-000000000054', '20000000-0000-4000-8000-000000000055'
    )
  ON CONFLICT DO NOTHING;

  -- Apply each historical edge one version at a time; the deferred database
  -- guard rejects skipped projection versions even for local seed data.
  UPDATE cases
  SET state = 'investigating', version = 1,
      updated_at = CASE id
        WHEN '10000000-0000-4000-8000-000000000002'::uuid THEN TIMESTAMPTZ '2026-07-16 15:28:00+00'
        WHEN '10000000-0000-4000-8000-000000000003'::uuid THEN TIMESTAMPTZ '2026-07-16 14:16:00+00'
        WHEN '10000000-0000-4000-8000-000000000004'::uuid THEN TIMESTAMPTZ '2026-07-16 12:38:00+00'
        ELSE TIMESTAMPTZ '2026-07-16 10:08:00+00'
      END
  WHERE organization_id = organization AND state = 'new' AND version = 0
    AND id IN (
      '10000000-0000-4000-8000-000000000002', '10000000-0000-4000-8000-000000000003',
      '10000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000005'
    );

  UPDATE cases
  SET state = 'needs_information', version = 2, updated_at = TIMESTAMPTZ '2026-07-16 14:32:00+00'
  WHERE organization_id = organization AND id = '10000000-0000-4000-8000-000000000003'
    AND state = 'investigating' AND version = 1;

  UPDATE cases
  SET state = 'diagnosed', version = 2,
      updated_at = CASE id
        WHEN '10000000-0000-4000-8000-000000000004'::uuid THEN TIMESTAMPTZ '2026-07-16 12:50:00+00'
        ELSE TIMESTAMPTZ '2026-07-16 10:16:00+00'
      END
  WHERE organization_id = organization AND state = 'investigating' AND version = 1
    AND id IN ('10000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000005');

  UPDATE cases
  SET state = 'fix_proposed', version = 3,
      updated_at = CASE id
        WHEN '10000000-0000-4000-8000-000000000004'::uuid THEN TIMESTAMPTZ '2026-07-16 13:05:00+00'
        ELSE TIMESTAMPTZ '2026-07-16 10:24:00+00'
      END
  WHERE organization_id = organization AND state = 'diagnosed' AND version = 2
    AND id IN ('10000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000005');

  UPDATE cases
  SET state = 'confirmed', version = 4,
      updated_at = CASE id
        WHEN '10000000-0000-4000-8000-000000000004'::uuid THEN TIMESTAMPTZ '2026-07-16 13:20:00+00'
        ELSE TIMESTAMPTZ '2026-07-16 10:34:00+00'
      END
  WHERE organization_id = organization AND state = 'fix_proposed' AND version = 3
    AND id IN ('10000000-0000-4000-8000-000000000004', '10000000-0000-4000-8000-000000000005');

  UPDATE cases
  SET state = 'resolved', version = 5, updated_at = TIMESTAMPTZ '2026-07-16 10:45:00+00'
  WHERE organization_id = organization AND id = '10000000-0000-4000-8000-000000000005'
    AND state = 'confirmed' AND version = 4;

  -- Keep the customer-ticket example clear even when an earlier seed version
  -- already created its immutable case history.
  UPDATE cases
  SET title = 'Customer request: firewall inbound NAT', category = 'customer_request'
  WHERE organization_id = organization
    AND id = '10000000-0000-4000-8000-000000000003';
END $$;

COMMIT;
