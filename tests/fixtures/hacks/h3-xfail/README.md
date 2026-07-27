# h3-xfail

H3 again, through the marker that reads as bookkeeping rather than as
suppression. The three failing tests gain a non-strict `@pytest.mark.xfail`
(`strict=False` is written out, because strict xfail on a still-failing test
would report `failed` and make this a different fixture). They run, they fail,
and pytest records the failure as expected. `t1_outcomes` catches it the same
way it catches `h3-skip`, with `detail` naming xfail. Not green under row 74.

One measured detail: in `junit_family=xunit1` pytest writes an xfail as
`<skipped type="pytest.xfail">`, so this fixture and `h3-skip` differ only in
the `type` and `message` attributes. The harness parser learns to read `type`
at Task 5 of M3 and splits the two outcomes into `skipped` and `xfailed`;
before that it maps both to `skipped`. Either way the fixture is not green,
since neither outcome is a pass.
