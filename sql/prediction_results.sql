BEGIN;
ALTER TABLE public.games ADD COLUMN IF NOT EXISTS scores_updated_at timestamptz;
ALTER TABLE public.games ADD COLUMN IF NOT EXISTS result_source text;
ALTER TABLE public.predictions ADD COLUMN IF NOT EXISTS probability_source text;
ALTER TABLE public.predictions ADD COLUMN IF NOT EXISTS analysis_version text;
COMMENT ON COLUMN public.predictions.model_probability IS 'Estimated outcome probability in [0,1]; provenance in probability_source. Not qualitative confidence.';
CREATE TABLE IF NOT EXISTS public.prediction_results (
 prediction_id bigint PRIMARY KEY REFERENCES public.predictions(id),
 outcome text NOT NULL CHECK (outcome IN ('pending','win','loss','push','void','needs_review')),
 stake numeric NOT NULL DEFAULT 100 CHECK (stake=100),
 profit numeric, home_score integer, away_score integer,
 source text, source_updated_at timestamptz,
 graded_at timestamptz NOT NULL DEFAULT now(),
 rule_version text NOT NULL DEFAULT 'flat100-v1',
 prediction_snapshot jsonb NOT NULL
);
CREATE TABLE IF NOT EXISTS public.prediction_result_history (
 id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 prediction_id bigint NOT NULL REFERENCES public.predictions(id),
 recorded_at timestamptz NOT NULL DEFAULT now(),
 previous_result jsonb, new_result jsonb NOT NULL
);
CREATE INDEX IF NOT EXISTS prediction_result_history_prediction_idx ON public.prediction_result_history(prediction_id,recorded_at);
CREATE INDEX IF NOT EXISTS predictions_game_idx ON public.predictions(game_id);
CREATE TABLE IF NOT EXISTS public.result_sync_status (
 name text PRIMARY KEY, checked_at timestamptz NOT NULL, completed_games integer NOT NULL, source text NOT NULL
);
CREATE OR REPLACE FUNCTION public.prediction_outcome(market text, selection text, line numeric, home_team text, away_team text, home_score integer, away_score integer)
RETURNS text LANGUAGE plpgsql IMMUTABLE SECURITY INVOKER SET search_path='' AS $$
DECLARE margin numeric;
BEGIN
 IF home_score IS NULL OR away_score IS NULL THEN RETURN 'pending'; END IF;
 IF home_score < 0 OR away_score < 0 THEN RETURN 'needs_review'; END IF;
 IF market IN ('moneyline','spread') THEN
  IF selection=home_team THEN margin:=home_score-away_score;
  ELSIF selection=away_team THEN margin:=away_score-home_score;
  ELSE RETURN 'needs_review'; END IF;
  IF market='spread' THEN
   IF line IS NULL THEN RETURN 'needs_review'; END IF;
   margin:=margin+line;
  END IF;
 ELSIF market IN ('total','totals') THEN
  IF line IS NULL OR lower(selection) NOT IN ('over','under') THEN RETURN 'needs_review'; END IF;
  margin:=home_score+away_score-line;
  IF lower(selection)='under' THEN margin:=-margin; END IF;
 ELSE RETURN 'needs_review'; END IF;
 IF margin>0 THEN RETURN 'win'; ELSIF margin<0 THEN RETURN 'loss'; ELSE RETURN 'push'; END IF;
END $$;
CREATE OR REPLACE FUNCTION public.grade_predictions_for_game(target_game text)
RETURNS void LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 INSERT INTO public.prediction_results AS old (prediction_id,outcome,stake,profit,home_score,away_score,source,source_updated_at,graded_at,rule_version,prediction_snapshot)
 SELECT p.id,o.outcome,100,
 CASE WHEN o.outcome='loss' THEN -100 WHEN o.outcome IN ('push','void') THEN 0
 WHEN o.outcome='win' AND abs(p.market_odds)>=100 THEN round(CASE WHEN p.market_odds<0 THEN 10000.0/abs(p.market_odds) ELSE p.market_odds::numeric END,2) END,
 g.home_score,g.away_score,g.result_source,g.scores_updated_at,now(),'flat100-v1',to_jsonb(p)
 FROM public.predictions p JOIN public.games g USING(game_id)
 CROSS JOIN LATERAL (SELECT CASE WHEN g.result_source IS NULL OR g.kickoff IS NULL OR g.kickoff>now() THEN 'pending'
 WHEN p.market_odds IS NULL OR abs(p.market_odds)<100 THEN 'needs_review'
 ELSE public.prediction_outcome(p.market,p.selection,p.market_line,g.home_team,g.away_team,g.home_score,g.away_score) END AS outcome) o
 WHERE p.game_id=target_game
 ON CONFLICT(prediction_id) DO UPDATE SET outcome=excluded.outcome,profit=excluded.profit,
 home_score=excluded.home_score,away_score=excluded.away_score,source=excluded.source,
 source_updated_at=excluded.source_updated_at,graded_at=excluded.graded_at,rule_version=excluded.rule_version,prediction_snapshot=excluded.prediction_snapshot
 WHERE (old.outcome,old.profit,old.home_score,old.away_score,old.source,old.rule_version,old.prediction_snapshot)
 IS DISTINCT FROM (excluded.outcome,excluded.profit,excluded.home_score,excluded.away_score,excluded.source,excluded.rule_version,excluded.prediction_snapshot);
END $$;
CREATE OR REPLACE FUNCTION public.audit_prediction_result() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 INSERT INTO public.prediction_result_history(prediction_id,previous_result,new_result)
 VALUES(NEW.prediction_id,CASE WHEN TG_OP='UPDATE' THEN to_jsonb(OLD) ELSE NULL END,to_jsonb(NEW));
 RETURN NEW;
END $$;
CREATE OR REPLACE FUNCTION public.refresh_prediction_result() RETURNS trigger LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
BEGIN
 PERFORM public.grade_predictions_for_game(NEW.game_id);
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS prediction_result_audit ON public.prediction_results;
CREATE TRIGGER prediction_result_audit AFTER INSERT OR UPDATE ON public.prediction_results FOR EACH ROW EXECUTE FUNCTION public.audit_prediction_result();
DROP TRIGGER IF EXISTS prediction_result_refresh ON public.predictions;
CREATE TRIGGER prediction_result_refresh AFTER INSERT OR UPDATE ON public.predictions FOR EACH ROW EXECUTE FUNCTION public.refresh_prediction_result();
DROP TRIGGER IF EXISTS game_result_refresh ON public.games;
CREATE TRIGGER game_result_refresh AFTER INSERT OR UPDATE OF home_score,away_score,result_source,scores_updated_at ON public.games FOR EACH ROW EXECUTE FUNCTION public.refresh_prediction_result();
CREATE OR REPLACE VIEW public.dashboard_predictions WITH (security_invoker=true) AS
 SELECT p.*,g.season,g.week,g.kickoff,g.away_team,g.home_team,
 coalesce((regexp_match(p.notes,'(?:^|;)\s*stage\s*=\s*([^;]+)'))[1],'unknown') AS stage,
 coalesce(p.notes ~* 'backfill=true',false) AS is_backfill,
 r.outcome,r.stake,r.profit,r.home_score,r.away_score,r.source AS result_source,r.source_updated_at,r.graded_at,r.rule_version
 FROM public.predictions p JOIN public.games g USING(game_id) LEFT JOIN public.prediction_results r ON r.prediction_id=p.id;
CREATE OR REPLACE VIEW public.dashboard_latest_odds WITH (security_invoker=true) AS
 SELECT DISTINCT ON(o.game_id,o.market,o.selection) o.game_id,o.market,o.selection,o.line,o.odds_american,o.captured_at
 FROM public.odds_history o JOIN public.games g USING(game_id)
 WHERE o.sportsbook='fanduel' AND o.is_live IS NOT TRUE AND g.season>=2026 AND o.player_id IS NULL
 ORDER BY o.game_id,o.market,o.selection,o.captured_at DESC,o.id DESC;
DO $$ DECLARE t record; BEGIN
 FOR t IN SELECT tablename FROM pg_tables WHERE schemaname='public' LOOP
  EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY',t.tablename);
 END LOOP;
END $$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM anon,authenticated;
REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM anon,authenticated;
REVOKE EXECUTE ON FUNCTION public.prediction_outcome(text,text,numeric,text,text,integer,integer),public.grade_predictions_for_game(text),public.audit_prediction_result(),public.refresh_prediction_result() FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION public.prediction_outcome(text,text,numeric,text,text,integer,integer),public.grade_predictions_for_game(text),public.audit_prediction_result(),public.refresh_prediction_result() TO service_role;
GRANT ALL ON public.prediction_results,public.prediction_result_history,public.result_sync_status TO service_role;
GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO service_role;
GRANT SELECT ON public.dashboard_predictions,public.dashboard_latest_odds TO service_role;
SELECT public.grade_predictions_for_game(game_id) FROM public.games WHERE game_id IN(SELECT game_id FROM public.predictions);
NOTIFY pgrst,'reload schema';
COMMIT;


