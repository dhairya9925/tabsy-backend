
-- Create groups table
CREATE TABLE public.groups (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  name text NOT NULL,
  description text,
  created_by uuid NOT NULL,
  created_at timestamp with time zone NOT NULL DEFAULT now(),
  updated_at timestamp with time zone NOT NULL DEFAULT now()
);

-- Create group_members table
CREATE TABLE public.group_members (
  id uuid NOT NULL DEFAULT gen_random_uuid() PRIMARY KEY,
  group_id uuid NOT NULL REFERENCES public.groups(id) ON DELETE CASCADE,
  user_id uuid NOT NULL,
  role text NOT NULL DEFAULT 'member',
  joined_at timestamp with time zone NOT NULL DEFAULT now(),
  UNIQUE(group_id, user_id)
);

-- Enable RLS
ALTER TABLE public.groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE public.group_members ENABLE ROW LEVEL SECURITY;

-- Groups policies: members can view their groups
CREATE POLICY "Members can view their groups"
  ON public.groups FOR SELECT TO authenticated
  USING (EXISTS (SELECT 1 FROM public.group_members WHERE group_members.group_id = groups.id AND group_members.user_id = auth.uid()));

-- Creator can update their groups
CREATE POLICY "Creator can update their groups"
  ON public.groups FOR UPDATE TO authenticated
  USING (created_by = auth.uid());

-- Creator can delete their groups
CREATE POLICY "Creator can delete their groups"
  ON public.groups FOR DELETE TO authenticated
  USING (created_by = auth.uid());

-- Authenticated users can create groups
CREATE POLICY "Authenticated users can create groups"
  ON public.groups FOR INSERT TO authenticated
  WITH CHECK (created_by = auth.uid());

-- Group members policies
CREATE POLICY "Members can view group members"
  ON public.group_members FOR SELECT TO authenticated
  USING (EXISTS (SELECT 1 FROM public.group_members AS gm WHERE gm.group_id = group_members.group_id AND gm.user_id = auth.uid()));

-- Group creator (admin) can add members
CREATE POLICY "Group admin can add members"
  ON public.group_members FOR INSERT TO authenticated
  WITH CHECK (EXISTS (SELECT 1 FROM public.groups WHERE groups.id = group_members.group_id AND groups.created_by = auth.uid())
    OR user_id = auth.uid());

-- Group creator can remove members
CREATE POLICY "Group admin can remove members"
  ON public.group_members FOR DELETE TO authenticated
  USING (EXISTS (SELECT 1 FROM public.groups WHERE groups.id = group_members.group_id AND groups.created_by = auth.uid())
    OR user_id = auth.uid());

-- Add updated_at trigger for groups
CREATE TRIGGER update_groups_updated_at
  BEFORE UPDATE ON public.groups
  FOR EACH ROW
  EXECUTE FUNCTION public.update_updated_at_column();
