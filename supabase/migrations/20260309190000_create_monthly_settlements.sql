CREATE TABLE public.monthly_settlements (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  group_id UUID NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
  month INTEGER NOT NULL CHECK (month >= 1 AND month <= 12),
  year INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'open' CHECK (status IN ('open', 'locked')),
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  UNIQUE(group_id, month, year)
);

CREATE TABLE public.member_monthly_status (
  id UUID NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  settlement_id UUID NOT NULL REFERENCES public.monthly_settlements(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
  status TEXT NOT NULL DEFAULT 'complete' CHECK (status IN ('complete')),
  created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT now(),
  UNIQUE(settlement_id, user_id)
);

-- RLS for monthly_settlements
ALTER TABLE public.monthly_settlements ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view monthly settlements for their groups"
  ON public.monthly_settlements FOR SELECT
  USING (public.is_group_member(group_id, auth.uid()));

CREATE POLICY "Users can insert monthly settlements for their groups"
  ON public.monthly_settlements FOR INSERT
  WITH CHECK (public.is_group_member(group_id, auth.uid()));

CREATE POLICY "Users can update monthly settlements for their groups"
  ON public.monthly_settlements FOR UPDATE
  USING (public.is_group_member(group_id, auth.uid()));

-- RLS for member_monthly_status
ALTER TABLE public.member_monthly_status ENABLE ROW LEVEL SECURITY;

CREATE POLICY "Users can view member status for their groups"
  ON public.member_monthly_status FOR SELECT
  USING (
    EXISTS (
      SELECT 1 FROM public.monthly_settlements s 
      WHERE s.id = member_monthly_status.settlement_id 
      AND public.is_group_member(s.group_id, auth.uid())
    )
  );

CREATE POLICY "Users can insert their own member status"
  ON public.member_monthly_status FOR INSERT
  WITH CHECK (
    auth.uid() = user_id AND
    EXISTS (
      SELECT 1 FROM public.monthly_settlements s 
      WHERE s.id = settlement_id 
      AND public.is_group_member(s.group_id, auth.uid())
    )
  );

CREATE POLICY "Users can delete their own member status"
  ON public.member_monthly_status FOR DELETE
  USING (
    auth.uid() = user_id AND
    EXISTS (
      SELECT 1 FROM public.monthly_settlements s 
      WHERE s.id = member_monthly_status.settlement_id 
      AND public.is_group_member(s.group_id, auth.uid())
    )
  );

-- timestamp update triggers
CREATE TRIGGER update_monthly_settlements_updated_at
  BEFORE UPDATE ON public.monthly_settlements
  FOR EACH ROW EXECUTE FUNCTION public.update_updated_at_column();
