ALTER TABLE "groups" DROP CONSTRAINT "groups_sponsor_id_profiles_user_id_fk";
--> statement-breakpoint
ALTER TABLE "groups" ALTER COLUMN "sponsor_id" SET DATA TYPE text;--> statement-breakpoint
ALTER TABLE "expenses" ADD COLUMN "edited_at" timestamp with time zone;--> statement-breakpoint
ALTER POLICY "Users can update their own expenses" ON "expenses" TO authenticated USING ((user_id = auth.uid()));--> statement-breakpoint
ALTER POLICY "Users can delete their own expenses" ON "expenses" TO authenticated USING ((user_id = auth.uid()));