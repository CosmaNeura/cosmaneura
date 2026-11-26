SELECT 'doctors_active' AS category, count(*) AS n FROM public.doctor WHERE deleted_at IS NULL
UNION ALL SELECT 'doctors_total', count(*) FROM public.doctor
UNION ALL SELECT 'practices_total', count(*) FROM public.practice
UNION ALL SELECT 'practices_baa_clickwrap_accepted', count(*) FROM public.practice WHERE baa_accepted_at IS NOT NULL
UNION ALL SELECT 'practices_negotiated_baa_on_file', count(*) FROM public.practice WHERE negotiated_baa_on_file
UNION ALL SELECT 'practices_subscription_status_set', count(*) FROM public.practice WHERE subscription_status IS NOT NULL
UNION ALL SELECT 'baa_acceptances_total', count(*) FROM public.baa_acceptance
UNION ALL SELECT 'practice_members_active', count(*) FROM public.practice_member WHERE access_revoked_at IS NULL
UNION ALL SELECT 'pilot_grants_active', count(*) FROM public.pilot_access_grandfather_grant WHERE revoked_at IS NULL