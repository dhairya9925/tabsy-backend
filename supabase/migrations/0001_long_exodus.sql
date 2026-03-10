CREATE TABLE "member_monthly_exclusions" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"group_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"month" integer NOT NULL,
	"year" integer NOT NULL,
	"exclusion_type" text DEFAULT 'partial' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now(),
	CONSTRAINT "member_monthly_exclusions_group_id_user_id_month_year_key" UNIQUE("group_id","user_id","month","year"),
	CONSTRAINT "member_monthly_exclusions_month_check" CHECK (month >= 1 AND month <= 12),
	CONSTRAINT "member_monthly_exclusions_year_check" CHECK (year >= 2020),
	CONSTRAINT "member_monthly_exclusions_type_check" CHECK (exclusion_type IN ('partial', 'full'))
);
--> statement-breakpoint
ALTER TABLE "member_monthly_exclusions" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "member_monthly_status" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"settlement_id" uuid NOT NULL,
	"user_id" uuid NOT NULL,
	"status" text DEFAULT 'complete' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "member_monthly_status_settlement_id_user_id_key" UNIQUE("settlement_id","user_id"),
	CONSTRAINT "member_monthly_status_status_check" CHECK (status IN ('complete'))
);
--> statement-breakpoint
ALTER TABLE "member_monthly_status" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
CREATE TABLE "monthly_settlements" (
	"id" uuid PRIMARY KEY DEFAULT gen_random_uuid() NOT NULL,
	"group_id" uuid NOT NULL,
	"month" integer NOT NULL,
	"year" integer NOT NULL,
	"status" text DEFAULT 'open' NOT NULL,
	"created_at" timestamp with time zone DEFAULT now() NOT NULL,
	"updated_at" timestamp with time zone DEFAULT now() NOT NULL,
	CONSTRAINT "monthly_settlements_group_id_month_year_key" UNIQUE("group_id","month","year"),
	CONSTRAINT "monthly_settlements_month_check" CHECK (month >= 1 AND month <= 12),
	CONSTRAINT "monthly_settlements_status_check" CHECK (status IN ('open', 'locked'))
);
--> statement-breakpoint
ALTER TABLE "monthly_settlements" ENABLE ROW LEVEL SECURITY;--> statement-breakpoint
ALTER TABLE "profiles" DROP CONSTRAINT "profiles_user_id_fkey";
--> statement-breakpoint
ALTER TABLE "expenses" DROP CONSTRAINT "expenses_paid_by_fkey";
--> statement-breakpoint
ALTER TABLE "expenses" DROP CONSTRAINT "expenses_user_id_fkey";
--> statement-breakpoint
ALTER TABLE "groups" ADD COLUMN "monthly_rent" numeric(10, 2) DEFAULT '0.00';--> statement-breakpoint
ALTER TABLE "member_monthly_exclusions" ADD CONSTRAINT "member_monthly_exclusions_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "public"."groups"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "member_monthly_status" ADD CONSTRAINT "member_monthly_status_settlement_id_fkey" FOREIGN KEY ("settlement_id") REFERENCES "public"."monthly_settlements"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
ALTER TABLE "monthly_settlements" ADD CONSTRAINT "monthly_settlements_group_id_fkey" FOREIGN KEY ("group_id") REFERENCES "public"."groups"("id") ON DELETE cascade ON UPDATE no action;--> statement-breakpoint
CREATE POLICY "Group members can view exclusions" ON "member_monthly_exclusions" AS PERMISSIVE FOR SELECT TO "authenticated" USING (is_group_member(group_id, auth.uid()));--> statement-breakpoint
CREATE POLICY "Group members can insert exclusions" ON "member_monthly_exclusions" AS PERMISSIVE FOR INSERT TO "authenticated" WITH CHECK (is_group_member(group_id, auth.uid()));--> statement-breakpoint
CREATE POLICY "Group members can delete exclusions" ON "member_monthly_exclusions" AS PERMISSIVE FOR DELETE TO "authenticated" USING (is_group_member(group_id, auth.uid()));--> statement-breakpoint
CREATE POLICY "Users can view member status for their groups" ON "member_monthly_status" AS PERMISSIVE FOR SELECT TO "authenticated";--> statement-breakpoint
CREATE POLICY "Users can insert their own member status" ON "member_monthly_status" AS PERMISSIVE FOR INSERT TO "authenticated";--> statement-breakpoint
CREATE POLICY "Users can delete their own member status" ON "member_monthly_status" AS PERMISSIVE FOR DELETE TO "authenticated";--> statement-breakpoint
CREATE POLICY "Users can view monthly settlements for their groups" ON "monthly_settlements" AS PERMISSIVE FOR SELECT TO "authenticated" USING (is_group_member(group_id, auth.uid()));--> statement-breakpoint
CREATE POLICY "Users can insert monthly settlements for their groups" ON "monthly_settlements" AS PERMISSIVE FOR INSERT TO "authenticated" WITH CHECK (is_group_member(group_id, auth.uid()));--> statement-breakpoint
CREATE POLICY "Users can update monthly settlements for their groups" ON "monthly_settlements" AS PERMISSIVE FOR UPDATE TO "authenticated" USING (is_group_member(group_id, auth.uid()));