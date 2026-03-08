-- Step 1: Create SECURITY DEFINER function to check group membership
CREATE OR REPLACE FUNCTION public.is_group_member(_group_id uuid, _user_id uuid)
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
  SELECT EXISTS (
    SELECT 1 FROM public.group_members
    WHERE group_id = _group_id AND user_id = _user_id
  )
$$;

-- Step 2: Fix the missing trigger
CREATE OR REPLACE TRIGGER on_auth_user_created
  AFTER INSERT ON auth.users
  FOR EACH ROW EXECUTE FUNCTION public.handle_new_user();

-- Step 3: Drop ALL existing RESTRICTIVE policies and recreate as PERMISSIVE

-- == group_members ==
DROP POLICY IF EXISTS "Members can view group members" ON group_members;
DROP POLICY IF EXISTS "Group admin can add members" ON group_members;
DROP POLICY IF EXISTS "Group admin can remove members" ON group_members;

CREATE POLICY "Members can view group members" ON group_members
  FOR SELECT TO authenticated
  USING (public.is_group_member(group_id, auth.uid()));

CREATE POLICY "Group admin can add members" ON group_members
  FOR INSERT TO authenticated
  WITH CHECK (
    (EXISTS (SELECT 1 FROM groups WHERE groups.id = group_members.group_id AND groups.created_by = auth.uid()))
    OR (user_id = auth.uid())
  );

CREATE POLICY "Group admin can remove members" ON group_members
  FOR DELETE TO authenticated
  USING (
    (EXISTS (SELECT 1 FROM groups WHERE groups.id = group_members.group_id AND groups.created_by = auth.uid()))
    OR (user_id = auth.uid())
  );

-- == groups ==
DROP POLICY IF EXISTS "Members can view their groups" ON groups;
DROP POLICY IF EXISTS "Authenticated users can create groups" ON groups;
DROP POLICY IF EXISTS "Creator can update their groups" ON groups;
DROP POLICY IF EXISTS "Creator can delete their groups" ON groups;

CREATE POLICY "Members can view their groups" ON groups
  FOR SELECT TO authenticated
  USING (public.is_group_member(id, auth.uid()));

CREATE POLICY "Authenticated users can create groups" ON groups
  FOR INSERT TO authenticated
  WITH CHECK (created_by = auth.uid());

CREATE POLICY "Creator can update their groups" ON groups
  FOR UPDATE TO authenticated
  USING (created_by = auth.uid());

CREATE POLICY "Creator can delete their groups" ON groups
  FOR DELETE TO authenticated
  USING (created_by = auth.uid());

-- == expenses ==
DROP POLICY IF EXISTS "Users can view their own expenses" ON expenses;
DROP POLICY IF EXISTS "Users can create their own expenses" ON expenses;
DROP POLICY IF EXISTS "Group members can view group expenses" ON expenses;
DROP POLICY IF EXISTS "Group members can create group expenses" ON expenses;
DROP POLICY IF EXISTS "Users can update their own expenses" ON expenses;
DROP POLICY IF EXISTS "Users can delete their own expenses" ON expenses;

CREATE POLICY "Users can view their own expenses" ON expenses
  FOR SELECT TO authenticated
  USING (auth.uid() = user_id);

CREATE POLICY "Group members can view group expenses" ON expenses
  FOR SELECT TO authenticated
  USING (group_id IS NOT NULL AND public.is_group_member(group_id, auth.uid()));

CREATE POLICY "Users can create their own expenses" ON expenses
  FOR INSERT TO authenticated
  WITH CHECK (auth.uid() = user_id AND group_id IS NULL);

CREATE POLICY "Group members can create group expenses" ON expenses
  FOR INSERT TO authenticated
  WITH CHECK (group_id IS NOT NULL AND user_id = auth.uid() AND public.is_group_member(group_id, auth.uid()));

CREATE POLICY "Users can update their own expenses" ON expenses
  FOR UPDATE TO authenticated
  USING (auth.uid() = user_id);

CREATE POLICY "Users can delete their own expenses" ON expenses
  FOR DELETE TO authenticated
  USING (auth.uid() = user_id);

-- == expense_splits ==
DROP POLICY IF EXISTS "Group members can view expense splits" ON expense_splits;
DROP POLICY IF EXISTS "Expense creator can insert splits" ON expense_splits;
DROP POLICY IF EXISTS "Expense creator can delete splits" ON expense_splits;
DROP POLICY IF EXISTS "Group members can update their splits" ON expense_splits;

CREATE POLICY "Group members can view expense splits" ON expense_splits
  FOR SELECT TO authenticated
  USING (EXISTS (
    SELECT 1 FROM expenses e
    WHERE e.id = expense_splits.expense_id
      AND e.group_id IS NOT NULL
      AND public.is_group_member(e.group_id, auth.uid())
  ));

CREATE POLICY "Expense creator can insert splits" ON expense_splits
  FOR INSERT TO authenticated
  WITH CHECK (EXISTS (
    SELECT 1 FROM expenses e
    WHERE e.id = expense_splits.expense_id AND e.user_id = auth.uid()
  ));

CREATE POLICY "Expense creator can delete splits" ON expense_splits
  FOR DELETE TO authenticated
  USING (EXISTS (
    SELECT 1 FROM expenses e
    WHERE e.id = expense_splits.expense_id AND e.user_id = auth.uid()
  ));

CREATE POLICY "Group members can update their splits" ON expense_splits
  FOR UPDATE TO authenticated
  USING (
    user_id = auth.uid()
    OR EXISTS (SELECT 1 FROM expenses e WHERE e.id = expense_splits.expense_id AND e.user_id = auth.uid())
  );

-- == profiles ==
DROP POLICY IF EXISTS "Users can view their own profile" ON profiles;
DROP POLICY IF EXISTS "Users can insert their own profile" ON profiles;
DROP POLICY IF EXISTS "Users can update their own profile" ON profiles;
DROP POLICY IF EXISTS "Users can view profiles of group members" ON profiles;

CREATE POLICY "Users can view their own profile" ON profiles
  FOR SELECT TO authenticated
  USING (auth.uid() = user_id);

CREATE POLICY "Users can view profiles of group members" ON profiles
  FOR SELECT TO authenticated
  USING (user_id IN (
    SELECT gm.user_id FROM group_members gm
    WHERE public.is_group_member(gm.group_id, auth.uid())
  ));

CREATE POLICY "Users can insert their own profile" ON profiles
  FOR INSERT TO authenticated
  WITH CHECK (auth.uid() = user_id);

CREATE POLICY "Users can update their own profile" ON profiles
  FOR UPDATE TO authenticated
  USING (auth.uid() = user_id);