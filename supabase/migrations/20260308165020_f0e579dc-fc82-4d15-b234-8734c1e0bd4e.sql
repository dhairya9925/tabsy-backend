
-- Drop all restrictive policies and recreate as permissive

-- EXPENSES
DROP POLICY IF EXISTS "Users can create their own expenses" ON expenses;
DROP POLICY IF EXISTS "Group members can create group expenses" ON expenses;
DROP POLICY IF EXISTS "Users can view their own expenses" ON expenses;
DROP POLICY IF EXISTS "Group members can view group expenses" ON expenses;
DROP POLICY IF EXISTS "Users can delete their own expenses" ON expenses;
DROP POLICY IF EXISTS "Users can update their own expenses" ON expenses;

CREATE POLICY "Users can create their own expenses" ON expenses FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id AND group_id IS NULL);
CREATE POLICY "Group members can create group expenses" ON expenses FOR INSERT TO authenticated WITH CHECK (group_id IS NOT NULL AND user_id = auth.uid() AND EXISTS (SELECT 1 FROM group_members gm WHERE gm.group_id = expenses.group_id AND gm.user_id = auth.uid()));
CREATE POLICY "Users can view their own expenses" ON expenses FOR SELECT TO authenticated USING (auth.uid() = user_id);
CREATE POLICY "Group members can view group expenses" ON expenses FOR SELECT TO authenticated USING (group_id IS NOT NULL AND EXISTS (SELECT 1 FROM group_members gm WHERE gm.group_id = expenses.group_id AND gm.user_id = auth.uid()));
CREATE POLICY "Users can delete their own expenses" ON expenses FOR DELETE TO authenticated USING (auth.uid() = user_id);
CREATE POLICY "Users can update their own expenses" ON expenses FOR UPDATE TO authenticated USING (auth.uid() = user_id);

-- GROUPS
DROP POLICY IF EXISTS "Authenticated users can create groups" ON groups;
DROP POLICY IF EXISTS "Members can view their groups" ON groups;
DROP POLICY IF EXISTS "Creator can update their groups" ON groups;
DROP POLICY IF EXISTS "Creator can delete their groups" ON groups;

CREATE POLICY "Authenticated users can create groups" ON groups FOR INSERT TO authenticated WITH CHECK (created_by = auth.uid());
CREATE POLICY "Members can view their groups" ON groups FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM group_members WHERE group_members.group_id = groups.id AND group_members.user_id = auth.uid()));
CREATE POLICY "Creator can update their groups" ON groups FOR UPDATE TO authenticated USING (created_by = auth.uid());
CREATE POLICY "Creator can delete their groups" ON groups FOR DELETE TO authenticated USING (created_by = auth.uid());

-- GROUP_MEMBERS
DROP POLICY IF EXISTS "Group admin can add members" ON group_members;
DROP POLICY IF EXISTS "Group admin can remove members" ON group_members;
DROP POLICY IF EXISTS "Members can view group members" ON group_members;

CREATE POLICY "Group admin can add members" ON group_members FOR INSERT TO authenticated WITH CHECK ((EXISTS (SELECT 1 FROM groups WHERE groups.id = group_members.group_id AND groups.created_by = auth.uid())) OR (user_id = auth.uid()));
CREATE POLICY "Group admin can remove members" ON group_members FOR DELETE TO authenticated USING ((EXISTS (SELECT 1 FROM groups WHERE groups.id = group_members.group_id AND groups.created_by = auth.uid())) OR (user_id = auth.uid()));
CREATE POLICY "Members can view group members" ON group_members FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM group_members gm WHERE gm.group_id = group_members.group_id AND gm.user_id = auth.uid()));

-- PROFILES
DROP POLICY IF EXISTS "Users can insert their own profile" ON profiles;
DROP POLICY IF EXISTS "Users can update their own profile" ON profiles;
DROP POLICY IF EXISTS "Users can view their own profile" ON profiles;
DROP POLICY IF EXISTS "Users can view profiles of group members" ON profiles;

CREATE POLICY "Users can insert their own profile" ON profiles FOR INSERT TO authenticated WITH CHECK (auth.uid() = user_id);
CREATE POLICY "Users can update their own profile" ON profiles FOR UPDATE TO authenticated USING (auth.uid() = user_id);
CREATE POLICY "Users can view their own profile" ON profiles FOR SELECT TO authenticated USING (auth.uid() = user_id);
CREATE POLICY "Users can view profiles of group members" ON profiles FOR SELECT TO authenticated USING (user_id IN (SELECT gm.user_id FROM group_members gm WHERE gm.group_id IN (SELECT gm2.group_id FROM group_members gm2 WHERE gm2.user_id = auth.uid())));

-- EXPENSE_SPLITS
DROP POLICY IF EXISTS "Expense creator can delete splits" ON expense_splits;
DROP POLICY IF EXISTS "Expense creator can insert splits" ON expense_splits;
DROP POLICY IF EXISTS "Group members can update their splits" ON expense_splits;
DROP POLICY IF EXISTS "Group members can view expense splits" ON expense_splits;

CREATE POLICY "Expense creator can delete splits" ON expense_splits FOR DELETE TO authenticated USING (EXISTS (SELECT 1 FROM expenses e WHERE e.id = expense_splits.expense_id AND e.user_id = auth.uid()));
CREATE POLICY "Expense creator can insert splits" ON expense_splits FOR INSERT TO authenticated WITH CHECK (EXISTS (SELECT 1 FROM expenses e WHERE e.id = expense_splits.expense_id AND e.user_id = auth.uid()));
CREATE POLICY "Group members can update their splits" ON expense_splits FOR UPDATE TO authenticated USING ((user_id = auth.uid()) OR (EXISTS (SELECT 1 FROM expenses e WHERE e.id = expense_splits.expense_id AND e.user_id = auth.uid())));
CREATE POLICY "Group members can view expense splits" ON expense_splits FOR SELECT TO authenticated USING (EXISTS (SELECT 1 FROM expenses e JOIN group_members gm ON gm.group_id = e.group_id WHERE e.id = expense_splits.expense_id AND gm.user_id = auth.uid()));
