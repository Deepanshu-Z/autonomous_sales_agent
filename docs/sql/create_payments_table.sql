-- payments: permanent ledger of CAPTURED Razorpay payments (one row per payment).
-- Written only by src/services/payment_ledger.py from Razorpay payment entities.
-- Run once in Supabase → SQL Editor.

create table if not exists public.payments (
  payment_id    text primary key       check (payment_id ~ '^pay_[A-Za-z0-9]+$'),
  phone         text not null          check (phone ~ '^[0-9]{10,15}$'),
  qr_id         text                   check (qr_id is null or qr_id ~ '^qr_[A-Za-z0-9]+$'),
  amount_inr    numeric(10, 2) not null check (amount_inr > 0),
  currency      text not null default 'INR',
  status        text not null          check (status = 'captured'),
  method        text,
  course_id     text not null          check (length(course_id) > 0),
  package       text,
  timing        text,
  teacher       text,
  student_email text                   check (student_email is null or student_email ~* '^[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,}$'),
  source        text not null          check (source in ('webhook', 'claim_lookup', 'reconciler', 'backfill')),
  captured_at   timestamptz not null,  -- Razorpay's created_at for the payment
  recorded_at   timestamptz not null default now(),
  updated_at    timestamptz not null default now()
);

create index if not exists payments_phone_idx on public.payments (phone);
create index if not exists payments_captured_at_idx on public.payments (captured_at desc);

-- Keep updated_at honest on every UPDATE (e.g. when the Gmail is attached later).
create or replace function public.payments_touch_updated_at() returns trigger
language plpgsql as $$
begin
  new.updated_at := now();
  return new;
end;
$$;

drop trigger if exists payments_touch_updated_at on public.payments;
create trigger payments_touch_updated_at
  before update on public.payments
  for each row execute function public.payments_touch_updated_at();

-- No anon/public access: only the server's service-role key (which bypasses RLS) can read/write.
alter table public.payments enable row level security;
