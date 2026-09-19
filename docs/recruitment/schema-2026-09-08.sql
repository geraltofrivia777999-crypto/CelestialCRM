--
-- PostgreSQL database dump
--

\restrict zxp8PR6XA9LWIRTYtlget0wHtdbynOjgNxfO6LnQt8pmi1NgecycgtXbXiS0MMC

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: criterion_mode; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.criterion_mode AS ENUM (
    'required',
    'preferred',
    'ignore'
);


--
-- Name: discovery_channel; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.discovery_channel AS ENUM (
    'search',
    'negotiation'
);


--
-- Name: provider_account_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.provider_account_status AS ENUM (
    'connected',
    'disconnected',
    'error'
);


--
-- Name: provider_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.provider_type AS ENUM (
    'hh'
);


--
-- Name: review_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.review_status AS ENUM (
    'pending',
    'added',
    'skipped'
);


--
-- Name: score_tier; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.score_tier AS ENUM (
    'low',
    'medium',
    'high',
    'hot'
);


--
-- Name: search_run_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.search_run_status AS ENUM (
    'queued',
    'running',
    'completed',
    'failed'
);


--
-- Name: search_run_trigger; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.search_run_trigger AS ENUM (
    'manual',
    'scheduled'
);


--
-- Name: source_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.source_type AS ENUM (
    'hh',
    'telegram'
);


--
-- Name: telegram_sync_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.telegram_sync_status AS ENUM (
    'pending',
    'synced',
    'failed'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: alembic_version; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alembic_version (
    version_num character varying(32) NOT NULL
);


--
-- Name: candidate_scores; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_scores (
    id uuid NOT NULL,
    external_candidate_id uuid NOT NULL,
    search_template_id uuid NOT NULL,
    score integer NOT NULL,
    tier public.score_tier NOT NULL,
    breakdown jsonb,
    hard_filters_passed boolean NOT NULL,
    computed_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: candidate_sources; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candidate_sources (
    id uuid NOT NULL,
    external_candidate_id uuid NOT NULL,
    source public.source_type NOT NULL,
    external_id character varying(255) NOT NULL,
    external_url character varying(1000),
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    via public.discovery_channel,
    seen_search_at timestamp with time zone,
    seen_response_at timestamp with time zone
);


--
-- Name: external_candidates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.external_candidates (
    id uuid NOT NULL,
    first_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    last_seen_at timestamp with time zone DEFAULT now() NOT NULL,
    raw_data jsonb,
    parsed_profile jsonb,
    crm_candidate_id character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    review_status public.review_status DEFAULT 'pending'::public.review_status NOT NULL,
    reviewed_at timestamp with time zone,
    reviewed_by character varying(255)
);


--
-- Name: integration_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.integration_logs (
    id uuid NOT NULL,
    occurred_at timestamp with time zone DEFAULT now() NOT NULL,
    event_type character varying(100) NOT NULL,
    level character varying(20) DEFAULT 'info'::character varying NOT NULL,
    source character varying(50),
    message text,
    context jsonb
);


--
-- Name: provider_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_accounts (
    id uuid NOT NULL,
    provider public.provider_type DEFAULT 'hh'::public.provider_type NOT NULL,
    label character varying(255),
    status public.provider_account_status DEFAULT 'disconnected'::public.provider_account_status NOT NULL,
    connected_at timestamp with time zone,
    meta jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: provider_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.provider_tokens (
    id uuid NOT NULL,
    provider_account_id uuid NOT NULL,
    access_token text NOT NULL,
    refresh_token text,
    expires_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: search_runs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_runs (
    id uuid NOT NULL,
    search_template_id uuid NOT NULL,
    trigger public.search_run_trigger NOT NULL,
    status public.search_run_status DEFAULT 'queued'::public.search_run_status NOT NULL,
    started_at timestamp with time zone,
    finished_at timestamp with time zone,
    stats jsonb,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: search_template_criteria; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_template_criteria (
    id uuid NOT NULL,
    search_template_id uuid NOT NULL,
    key character varying(100) NOT NULL,
    value character varying(500) NOT NULL,
    mode public.criterion_mode NOT NULL,
    weight integer DEFAULT 0 NOT NULL
);


--
-- Name: search_templates; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.search_templates (
    id uuid NOT NULL,
    name character varying(255) NOT NULL,
    crm_vacancy_id character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    auto_search_enabled boolean DEFAULT false NOT NULL,
    interval_minutes integer,
    score_thresholds jsonb,
    last_run_at timestamp with time zone,
    next_run_at timestamp with time zone,
    last_success_at timestamp with time zone,
    last_error text,
    created_by character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    hh_vacancy_id character varying(255),
    template_type character varying(20) DEFAULT 'search'::character varying NOT NULL
);


--
-- Name: telegram_applications; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telegram_applications (
    id uuid NOT NULL,
    telegram_user_id bigint NOT NULL,
    vacancy_ref character varying(255),
    candidate_text text,
    resume_file_ref character varying(500),
    received_at timestamp with time zone DEFAULT now() NOT NULL,
    external_candidate_id uuid,
    sync_status public.telegram_sync_status DEFAULT 'pending'::public.telegram_sync_status NOT NULL
);


--
-- Name: alembic_version alembic_version_pkc; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alembic_version
    ADD CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num);


--
-- Name: candidate_scores candidate_scores_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_scores
    ADD CONSTRAINT candidate_scores_pkey PRIMARY KEY (id);


--
-- Name: candidate_sources candidate_sources_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_sources
    ADD CONSTRAINT candidate_sources_pkey PRIMARY KEY (id);


--
-- Name: external_candidates external_candidates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.external_candidates
    ADD CONSTRAINT external_candidates_pkey PRIMARY KEY (id);


--
-- Name: integration_logs integration_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.integration_logs
    ADD CONSTRAINT integration_logs_pkey PRIMARY KEY (id);


--
-- Name: provider_accounts provider_accounts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_accounts
    ADD CONSTRAINT provider_accounts_pkey PRIMARY KEY (id);


--
-- Name: provider_tokens provider_tokens_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_tokens
    ADD CONSTRAINT provider_tokens_pkey PRIMARY KEY (id);


--
-- Name: search_runs search_runs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_runs
    ADD CONSTRAINT search_runs_pkey PRIMARY KEY (id);


--
-- Name: search_template_criteria search_template_criteria_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_template_criteria
    ADD CONSTRAINT search_template_criteria_pkey PRIMARY KEY (id);


--
-- Name: search_templates search_templates_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_templates
    ADD CONSTRAINT search_templates_pkey PRIMARY KEY (id);


--
-- Name: telegram_applications telegram_applications_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_applications
    ADD CONSTRAINT telegram_applications_pkey PRIMARY KEY (id);


--
-- Name: candidate_scores uq_candidate_scores_candidate_template; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_scores
    ADD CONSTRAINT uq_candidate_scores_candidate_template UNIQUE (external_candidate_id, search_template_id);


--
-- Name: candidate_sources uq_candidate_sources_source_external_id; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_sources
    ADD CONSTRAINT uq_candidate_sources_source_external_id UNIQUE (source, external_id);


--
-- Name: ix_candidate_scores_external_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_scores_external_candidate_id ON public.candidate_scores USING btree (external_candidate_id);


--
-- Name: ix_candidate_scores_search_template_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_scores_search_template_id ON public.candidate_scores USING btree (search_template_id);


--
-- Name: ix_candidate_sources_external_candidate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_candidate_sources_external_candidate_id ON public.candidate_sources USING btree (external_candidate_id);


--
-- Name: ix_external_candidates_review_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_external_candidates_review_status ON public.external_candidates USING btree (review_status);


--
-- Name: ix_integration_logs_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integration_logs_event_type ON public.integration_logs USING btree (event_type);


--
-- Name: ix_integration_logs_occurred_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_integration_logs_occurred_at ON public.integration_logs USING btree (occurred_at);


--
-- Name: ix_provider_tokens_provider_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_provider_tokens_provider_account_id ON public.provider_tokens USING btree (provider_account_id);


--
-- Name: ix_search_runs_search_template_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_search_runs_search_template_id ON public.search_runs USING btree (search_template_id);


--
-- Name: ix_search_runs_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_search_runs_status ON public.search_runs USING btree (status);


--
-- Name: ix_search_template_criteria_search_template_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX ix_search_template_criteria_search_template_id ON public.search_template_criteria USING btree (search_template_id);


--
-- Name: candidate_scores candidate_scores_external_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_scores
    ADD CONSTRAINT candidate_scores_external_candidate_id_fkey FOREIGN KEY (external_candidate_id) REFERENCES public.external_candidates(id) ON DELETE CASCADE;


--
-- Name: candidate_scores candidate_scores_search_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_scores
    ADD CONSTRAINT candidate_scores_search_template_id_fkey FOREIGN KEY (search_template_id) REFERENCES public.search_templates(id) ON DELETE CASCADE;


--
-- Name: candidate_sources candidate_sources_external_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candidate_sources
    ADD CONSTRAINT candidate_sources_external_candidate_id_fkey FOREIGN KEY (external_candidate_id) REFERENCES public.external_candidates(id) ON DELETE CASCADE;


--
-- Name: provider_tokens provider_tokens_provider_account_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.provider_tokens
    ADD CONSTRAINT provider_tokens_provider_account_id_fkey FOREIGN KEY (provider_account_id) REFERENCES public.provider_accounts(id) ON DELETE CASCADE;


--
-- Name: search_runs search_runs_search_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_runs
    ADD CONSTRAINT search_runs_search_template_id_fkey FOREIGN KEY (search_template_id) REFERENCES public.search_templates(id) ON DELETE CASCADE;


--
-- Name: search_template_criteria search_template_criteria_search_template_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.search_template_criteria
    ADD CONSTRAINT search_template_criteria_search_template_id_fkey FOREIGN KEY (search_template_id) REFERENCES public.search_templates(id) ON DELETE CASCADE;


--
-- Name: telegram_applications telegram_applications_external_candidate_id_fkey; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telegram_applications
    ADD CONSTRAINT telegram_applications_external_candidate_id_fkey FOREIGN KEY (external_candidate_id) REFERENCES public.external_candidates(id) ON DELETE SET NULL;


--
-- PostgreSQL database dump complete
--

\unrestrict zxp8PR6XA9LWIRTYtlget0wHtdbynOjgNxfO6LnQt8pmi1NgecycgtXbXiS0MMC
