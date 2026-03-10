-- Add monthly_rent to groups table
ALTER TABLE public.groups
ADD COLUMN monthly_rent NUMERIC(10, 2) DEFAULT 0.00;
